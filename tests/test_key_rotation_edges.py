from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import key_rotation
from app.services.key_rotation import RotationReport, rotate_master_key


class ScalarResult:
    """Вернуть подготовленную коллекцию из scalars().all()."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить коллекцию."""
        self.values = values

    def all(self) -> list[object]:
        """Вернуть коллекцию без изменений."""
        return self.values


class RotationSession:
    """Предоставить последовательности encrypted rows и flush counter."""

    def __init__(self, rows: list[list[object]]) -> None:
        """Сохранить результаты последовательных scalars запросов."""
        self.rows = list(rows)
        self.flush_calls = 0

    def scalars(self, _statement: object) -> ScalarResult:
        """Вернуть следующий набор encrypted rows."""
        return ScalarResult(self.rows.pop(0) if self.rows else [])

    def flush(self) -> None:
        """Зафиксировать flush."""
        self.flush_calls += 1


class PrefixCipher:
    """Имитировать context-bound decrypt/encrypt без криптографии."""

    def __init__(self, prefix: str) -> None:
        """Сохранить префикс нового ciphertext."""
        self.prefix = prefix

    def decrypt(self, value: str, *, context: str) -> str:
        """Вернуть детерминированный plaintext."""
        assert value and context
        return f"plain:{context}"

    def encrypt(self, value: str, *, context: str) -> str:
        """Вернуть новый context-bound ciphertext."""
        assert value
        return f"{self.prefix}:{context}"


class RotationStorage:
    """Хранить privacy export и журнал замен/восстановлений."""

    def __init__(self, *, fail_on_put: int | None = None) -> None:
        """Настроить необязательный номер падающего put."""
        self.fail_on_put = fail_on_put
        self.put_calls = 0
        self.values = {"privacy/export.enc": b"old-ciphertext"}

    def read_bytes(self, key: str) -> bytes:
        """Прочитать исходный privacy export."""
        return self.values[key]

    def put_bytes(self, key: str, value: bytes, *, content_type: str) -> None:
        """Сохранить replacement/original либо имитировать отказ object storage."""
        assert content_type == "application/octet-stream"
        self.put_calls += 1
        if self.fail_on_put == self.put_calls:
            raise OSError("storage restore failed")
        self.values[key] = value


def test_rotation_report_is_sorted_and_dry_run_preflights_privacy_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить report serialization, empty field skip и privacy export preflight."""
    report = RotationReport(
        fields={"z": 1, "a": 2}, privacy_exports=1, total_values=4, dry_run=True
    )
    assert list(report.to_dict()["fields"]) == ["a", "z"]

    monkeypatch.setattr(key_rotation, "FIELD_SPECS", (key_rotation.FIELD_SPECS[0],))
    empty_secret = SimpleNamespace(id="user-id", totp_secret_enc="")
    export = SimpleNamespace(id="privacy-id", output_relative_path="privacy/export.enc")
    storage = RotationStorage()
    rotated = rotate_master_key(
        RotationSession([[empty_secret], [export]]),  # type: ignore[arg-type]
        old_cipher=PrefixCipher("old"),  # type: ignore[arg-type]
        new_cipher=PrefixCipher("new"),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        dry_run=True,
    )
    assert rotated.privacy_exports == 1
    assert rotated.total_values == 1
    assert rotated.fields == {}
    assert storage.put_calls == 0


def test_rotation_applies_database_and_storage_replacements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить применение field/export replacements и audit flush."""
    monkeypatch.setattr(key_rotation, "FIELD_SPECS", (key_rotation.FIELD_SPECS[0],))
    monkeypatch.setattr(key_rotation, "write_audit", lambda *_args, **_kwargs: None)
    secret = SimpleNamespace(id="user-id", totp_secret_enc="old-secret")
    export = SimpleNamespace(id="privacy-id", output_relative_path="privacy/export.enc")
    db = RotationSession([[secret], [export]])
    storage = RotationStorage()
    report = rotate_master_key(
        db,  # type: ignore[arg-type]
        old_cipher=PrefixCipher("old"),  # type: ignore[arg-type]
        new_cipher=PrefixCipher("new"),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
    )
    assert secret.totp_secret_enc == "new:user-totp:user-id"
    assert storage.values["privacy/export.enc"] == b"new:privacy-export:privacy-id"
    assert report.fields == {"user.totp": 1}
    assert report.total_values == 2
    assert db.flush_calls == 1


@pytest.mark.parametrize("restore_fails", [False, True])
def test_rotation_restores_export_and_preserves_original_failure(
    monkeypatch: pytest.MonkeyPatch,
    restore_fails: bool,
) -> None:
    """Проверить best-effort restore и сохранение исходной audit ошибки."""
    monkeypatch.setattr(key_rotation, "FIELD_SPECS", ())
    monkeypatch.setattr(
        key_rotation,
        "write_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")),
    )
    export = SimpleNamespace(id="privacy-id", output_relative_path="privacy/export.enc")
    storage = RotationStorage(fail_on_put=2 if restore_fails else None)
    with pytest.raises(RuntimeError, match="audit failed"):
        rotate_master_key(
            RotationSession([[export]]),  # type: ignore[arg-type]
            old_cipher=PrefixCipher("old"),  # type: ignore[arg-type]
            new_cipher=PrefixCipher("new"),  # type: ignore[arg-type]
            storage=storage,  # type: ignore[arg-type]
        )
    assert storage.put_calls == 2
    if not restore_fails:
        assert storage.values["privacy/export.enc"] == b"old-ciphertext"
