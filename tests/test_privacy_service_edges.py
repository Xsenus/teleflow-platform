from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.enums import PrivacyRequestStatus, PrivacyRequestType, RecoveryBackupStatus
from app.services import privacy
from app.services.privacy import PrivacyService, RetentionService


class ScalarResult:
    """Вернуть подготовленную коллекцию scalars().all()."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить коллекцию."""
        self.values = values

    def all(self) -> list[object]:
        """Вернуть подготовленные строки как результат SQLAlchemy scalars().all()."""
        return self.values


class PrivacySession:
    """Предоставить scalar/scalars и mutation contract privacy worker."""

    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        scalar_lists: list[list[object]] | None = None,
        get_value: object | None = None,
    ) -> None:
        """Сохранить ответы запросов и mutation counters."""
        self.scalar_values = list(scalar_values or [])
        self.scalar_lists = list(scalar_lists or [])
        self.get_value = get_value
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.executed = 0
        self.deleted: list[object] = []

    def __enter__(self) -> PrivacySession:
        """Открыть fake session context."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Закрыть fake session context."""

    def scalar(self, _statement: object) -> object | None:
        """Вернуть следующий scalar."""
        return self.scalar_values.pop(0) if self.scalar_values else None

    def scalars(self, _statement: object) -> ScalarResult:
        """Вернуть следующую коллекцию."""
        return ScalarResult(self.scalar_lists.pop(0) if self.scalar_lists else [])

    def get(self, _model: object, _key: object) -> object | None:
        """Вернуть объект после rollback."""
        return self.get_value

    def commit(self) -> None:
        """Зафиксировать commit."""
        self.commits += 1

    def rollback(self) -> None:
        """Зафиксировать rollback."""
        self.rollbacks += 1

    def flush(self) -> None:
        """Зафиксировать flush."""
        self.flushes += 1

    def execute(self, _statement: object) -> None:
        """Зафиксировать bulk delete AI interactions."""
        self.executed += 1

    def delete(self, item: object) -> None:
        """Зафиксировать удаляемую conversation."""
        self.deleted.append(item)


class PrivacyStorage:
    """Хранить encrypted exports и фиксировать удаления."""

    def __init__(self, *, delete_fails: bool = False) -> None:
        """Настроить необязательную ошибку удаления."""
        self.delete_fails = delete_fails
        self.values: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_bytes(self, key: str, value: bytes, *, content_type: str) -> None:
        """Сохранить export bytes."""
        assert content_type
        self.values[key] = value

    def read_bytes(self, key: str) -> bytes:
        """Прочитать export bytes."""
        return self.values[key]

    def delete(self, key: str) -> None:
        """Удалить export либо имитировать отказ storage."""
        self.deleted.append(key)
        if self.delete_fails:
            raise OSError("delete failed")
        self.values.pop(key, None)


class PrivacyCipher:
    """Имитировать context-bound privacy encryption."""

    def encrypt(self, value: str, *, context: str) -> str:
        """Вернуть encrypted marker."""
        return f"encrypted:{context}:{value}"

    def decrypt(self, value: str, *, context: str) -> str:
        """Вернуть plaintext или controlled failure."""
        if value == "bad":
            raise ValueError("decrypt failed")
        return value.removeprefix(f"encrypted:{context}:")

    def decrypt_json(self, value: str, *, context: str) -> dict[str, object]:
        """Вернуть payload для совпадения privacy identifiers."""
        assert context
        if value == "bad":
            raise ValueError("decrypt failed")
        return {"chat": 100, "user": 200}


def privacy_settings(**overrides: object) -> SimpleNamespace:
    """Создать минимальные privacy/retention настройки."""
    values = {
        "privacy_export_ttl_hours": 24,
        "max_export_bytes": 1_000_000,
        "inbound_raw_retention_days": 7,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def service(session: PrivacySession, storage: PrivacyStorage | None = None) -> PrivacyService:
    """Создать PrivacyService на fake dependencies."""
    return PrivacyService(
        lambda: session,  # type: ignore[arg-type]
        privacy_settings(),  # type: ignore[arg-type]
        PrivacyCipher(),  # type: ignore[arg-type]
        storage or PrivacyStorage(),  # type: ignore[arg-type]
    )


def test_process_next_handles_empty_queue_and_dispatches_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить idle worker и передачу найденной pending request в process."""
    assert service(PrivacySession()).process_next() is False
    item = object()
    worker = service(PrivacySession(scalar_values=[item]))
    calls: list[object] = []
    monkeypatch.setattr(worker, "process", lambda _db, value: calls.append(value))
    assert worker.process_next() is True
    assert calls == [item]


def test_process_failure_marks_reloaded_request_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить rollback, bounded error и failure audit после service exception."""
    failed = SimpleNamespace(
        id="request-id",
        organization_id="organization-id",
        status=PrivacyRequestStatus.PROCESSING,
        error_message=None,
        processed_at=None,
    )
    item = SimpleNamespace(
        id="request-id",
        organization_id="organization-id",
        request_type=PrivacyRequestType.EXPORT,
        status=PrivacyRequestStatus.PENDING,
    )
    db = PrivacySession(get_value=failed)
    worker = service(db)
    monkeypatch.setattr(
        worker,
        "_find_conversations",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("export failed")),
    )
    monkeypatch.setattr(privacy, "write_audit", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="export failed"):
        worker.process(db, item)  # type: ignore[arg-type]
    assert db.rollbacks == 1
    assert failed.status == PrivacyRequestStatus.FAILED
    assert failed.error_message == "export failed"
    assert db.commits == 2


def test_read_export_expires_and_removes_storage_object() -> None:
    """Проверить TTL export и немедленное удаление expired ciphertext."""
    storage = PrivacyStorage()
    worker = service(PrivacySession(), storage)
    item = SimpleNamespace(
        id="request-id",
        status=PrivacyRequestStatus.COMPLETED,
        output_relative_path="exports/old.enc",
        processed_at=datetime.now(UTC) - timedelta(days=2),
    )
    with pytest.raises(FileNotFoundError, match="истёк"):
        worker.read_export(item)  # type: ignore[arg-type]
    assert storage.deleted == ["exports/old.enc"]


def test_find_conversations_handles_all_selectors_and_empty_request() -> None:
    """Проверить no-selector short circuit и chat/user/conversation OR selector."""
    db = PrivacySession(scalar_lists=[[object()]])
    worker = service(db)
    empty = SimpleNamespace(
        organization_id="organization-id",
        conversation_id=None,
        telegram_user_id=None,
        telegram_chat_id=None,
    )
    assert worker._find_conversations(db, empty) == []  # type: ignore[arg-type]
    selected = SimpleNamespace(
        organization_id="organization-id",
        conversation_id="conversation-id",
        telegram_user_id=200,
        telegram_chat_id=100,
    )
    assert len(worker._find_conversations(db, selected)) == 1  # type: ignore[arg-type]


def test_export_size_and_optional_decryption_fail_closed() -> None:
    """Проверить max export bytes и безопасный marker при повреждённом ciphertext."""
    db = PrivacySession()
    worker = PrivacyService(
        lambda: db,  # type: ignore[arg-type]
        privacy_settings(max_export_bytes=1),  # type: ignore[arg-type]
        PrivacyCipher(),  # type: ignore[arg-type]
        PrivacyStorage(),  # type: ignore[arg-type]
    )
    item = SimpleNamespace(id="request-id", organization_id="organization-id")
    with pytest.raises(ValueError, match="системный лимит"):
        worker._export(db, item, [])  # type: ignore[arg-type]
    assert worker._decrypt_optional(None, "context") is None
    assert worker._decrypt_optional("bad", "context") == "[DECRYPTION_FAILED]"


def test_delete_scrubs_matching_updates_and_ignores_empty_or_corrupted_payloads() -> None:
    """Проверить empty delete и scrub raw updates при совпадении chat/user identifiers."""
    worker = service(PrivacySession())
    item = SimpleNamespace(
        organization_id="organization-id",
        conversation_id="conversation-id",
        details={},
    )
    empty_db = PrivacySession()
    worker._delete(empty_db, item, [])  # type: ignore[arg-type]
    assert empty_db.flushes == 0

    updates = [
        SimpleNamespace(id="empty", payload_enc=None, payload_redacted={}),
        SimpleNamespace(id="bad", payload_enc="bad", payload_redacted={}),
        SimpleNamespace(id="match", payload_enc="good", payload_redacted={}),
    ]
    conversation = SimpleNamespace(id="conversation-id", telegram_chat_id=100, telegram_user_id=200)
    db = PrivacySession(scalar_lists=[updates])
    worker._delete(db, item, [conversation])  # type: ignore[arg-type]
    assert updates[2].payload_enc is None
    assert updates[2].payload_redacted == {"privacy_deleted": True}
    assert db.executed == 1
    assert db.deleted == [conversation]
    assert item.conversation_id is None


def test_retention_scrubs_candidate_and_survives_export_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить PII scrub, expired export cleanup и recovery evidence expiry."""
    message = SimpleNamespace(body_enc="secret", body_preview="preview")
    candidate = SimpleNamespace(phone_enc="phone", email_enc="email", structured_data={"pii": 1})
    conversation = SimpleNamespace(
        messages=[message],
        candidate=candidate,
        first_name="Name",
        last_name="Last",
        username="user",
        metadata_json={},
        retention_until=datetime.now(UTC) - timedelta(days=1),
    )
    update = SimpleNamespace(payload_enc="raw")
    missing_path = SimpleNamespace(output_relative_path=None)
    failing_path = SimpleNamespace(output_relative_path="exports/fail.enc")
    recovery = SimpleNamespace(
        status=RecoveryBackupStatus.VERIFIED,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        error_message=None,
    )
    db = PrivacySession(
        scalar_lists=[
            [conversation],
            [update],
            [missing_path, failing_path],
            [recovery],
        ]
    )
    storage = PrivacyStorage(delete_fails=True)
    monkeypatch.setattr(privacy, "expire_support_bundles", lambda *_args, **_kwargs: 2)
    retention = RetentionService(
        lambda: db,  # type: ignore[arg-type]
        privacy_settings(),  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
    )
    stats = retention.run()
    assert candidate.phone_enc is None and candidate.email_enc is None
    assert candidate.structured_data == {}
    assert message.body_enc is None
    assert update.payload_enc is None
    assert failing_path.output_relative_path is None
    assert recovery.status == RecoveryBackupStatus.EXPIRED
    assert stats == {
        "conversations_scrubbed": 1,
        "updates_scrubbed": 1,
        "exports_deleted": 1,
        "support_bundles_deleted": 2,
        "recovery_evidence_expired": 1,
    }
