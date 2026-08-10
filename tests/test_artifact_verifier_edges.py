from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from typing import Any

import pytest

from app.services import artifact_verifier
from app.services.artifact_verifier import (
    ArtifactVerificationError,
    _apply_local_trust,
    _canonical_json,
    _inspect_acceptance_json,
    _inspect_configuration_zip,
    _inspect_support_zip,
    _parse_sha256_manifest,
    _read_zip,
    _safe_path,
    _signature_envelope,
    inspect_artifact,
)


def zip_bytes(entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    """Сформировать ZIP с сохранением порядка и возможностью повторяющихся имён."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return buffer.getvalue()


def configuration_files(*, extra: dict[str, bytes] | None = None) -> dict[str, bytes]:
    """Вернуть минимальный конфигурационный архив с согласованным manifest."""
    document = {"product": "TeleFlow Platform", "entities": {}}
    payloads = {"bundle.json": _canonical_json(document), **(extra or {})}
    manifest = {
        "product": "TeleFlow Platform",
        "schema_version": 1,
        "source_product_version": "2.5.0",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
    }
    return {**payloads, "manifest.json": _canonical_json(manifest)}


def acceptance_bytes(*, program: Any = None, stages: Any = None) -> bytes:
    """Сформировать минимальный неподписанный acceptance report с корректным SHA-256."""
    payload = {
        "product": "TeleFlow Platform",
        "report_schema_version": 1,
        "generated_at": "2026-08-10T00:00:00+00:00",
        "program": {"id": "program-1", "status": "accepted"} if program is None else program,
        "stages": [] if stages is None else stages,
    }
    manifest = {
        "algorithm": "SHA-256",
        "payload_sha256": hashlib.sha256(_canonical_json(payload)).hexdigest(),
        "generated_at": payload["generated_at"],
    }
    return _canonical_json({"payload": payload, "manifest": manifest, "signature": None})


def test_artifact_path_and_zip_container_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить безопасные пути, повреждённый ZIP, дубликаты, symlink и лимиты распаковки."""
    assert _safe_path("folder/file.json") == "folder/file.json"
    for raw in ("", "/absolute", "\\absolute", "folder/../escape"):
        with pytest.raises(ArtifactVerificationError):
            _safe_path(raw)
    with pytest.raises(ArtifactVerificationError, match="повреждён"):
        _read_zip(b"PK\x03\x04broken", max_bytes=100)
    with pytest.raises(ArtifactVerificationError, match="повторяющиеся"):
        _read_zip(zip_bytes([("a.txt", b"1"), ("A.TXT", b"2")]), max_bytes=100)

    symlink = zipfile.ZipInfo("link")
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ArtifactVerificationError, match="Символические"):
        _read_zip(zip_bytes([(symlink, b"target")]), max_bytes=100)
    with pytest.raises(ArtifactVerificationError, match="превышает лимит"):
        _read_zip(zip_bytes([("large.bin", b"12345")]), max_bytes=4)

    monkeypatch.setattr(artifact_verifier, "MAX_ARCHIVE_FILES", 0)
    with pytest.raises(ArtifactVerificationError, match="слишком много"):
        _read_zip(zip_bytes([("one.txt", b"1")]), max_bytes=100)


def test_zip_reader_skips_directories_and_wraps_read_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить пропуск directory entry и преобразование ошибок чтения ZIP."""
    directory = zipfile.ZipInfo("folder/")
    directory.external_attr = (stat.S_IFDIR | 0o755) << 16
    assert _read_zip(zip_bytes([(directory, b""), ("folder/a.txt", b"ok")]), max_bytes=10) == {
        "folder/a.txt": b"ok"
    }

    class EncryptedArchive:
        """Имитировать ZIP с установленным encrypted flag без создания реального шифрования."""

        def __enter__(self):
            """Вернуть тестовый архив для контекстного менеджера."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Завершить контекст без подавления исключений."""

        def infolist(self) -> list[zipfile.ZipInfo]:
            """Вернуть entry с битом традиционного ZIP-шифрования."""
            info = zipfile.ZipInfo("encrypted.txt")
            info.flag_bits = 0x1
            return [info]

    monkeypatch.setattr(zipfile, "ZipFile", lambda *_args, **_kwargs: EncryptedArchive())
    with pytest.raises(ArtifactVerificationError, match="Зашифрованные"):
        _read_zip(b"anything", max_bytes=100)

    class BrokenArchive:
        """Имитировать архив, который ломается после успешного чтения каталога entries."""

        def __enter__(self):
            """Вернуть тестовый архив для контекстного менеджера."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Завершить контекст без подавления исключений."""

        def infolist(self) -> list[zipfile.ZipInfo]:
            """Вернуть единственный обычный файл."""
            return [zipfile.ZipInfo("broken.txt")]

        def read(self, _info: zipfile.ZipInfo) -> bytes:
            """Сымитировать CRC-ошибку чтения содержимого."""
            raise zipfile.BadZipFile("broken payload")

    monkeypatch.setattr(zipfile, "ZipFile", lambda *_args, **_kwargs: BrokenArchive())
    with pytest.raises(ArtifactVerificationError, match="Не удалось прочитать broken.txt"):
        _read_zip(b"anything", max_bytes=100)


def test_signature_envelope_and_local_trust_rules() -> None:
    """Проверить форму SIGNATURE.json и локальное назначение доверенного статуса."""
    assert _signature_envelope({}) is None
    with pytest.raises(ArtifactVerificationError, match="повреждён"):
        _signature_envelope({"SIGNATURE.json": b"{"})
    with pytest.raises(ArtifactVerificationError, match="JSON-объект"):
        _signature_envelope({"SIGNATURE.json": b"[]"})
    assert _signature_envelope({"SIGNATURE.json": b'{"key_id":"x"}'}) == {"key_id": "x"}

    untrusted = _apply_local_trust(
        {"cryptographically_valid": True, "fingerprint": "ABC", "status": "valid_untrusted"},
        trusted_fingerprints=set(),
    )
    trusted = _apply_local_trust(
        {"cryptographically_valid": True, "fingerprint": "ABC", "status": "valid_untrusted"},
        trusted_fingerprints={"abc"},
    )
    invalid = _apply_local_trust(
        {"cryptographically_valid": False, "fingerprint": "abc"},
        trusted_fingerprints={"abc"},
    )
    assert untrusted["trusted"] is False
    assert trusted["trusted"] is True and trusted["status"] == "valid_trusted"
    assert invalid["trusted"] is False


def test_configuration_artifact_success_and_contract_errors() -> None:
    """Проверить целостность configuration bundle и все основные нарушения manifest."""
    valid = configuration_files(extra={"media/file.bin": b"payload"})
    result = _inspect_configuration_zip(valid, trusted_fingerprints=set())
    assert result["artifact_type"] == "configuration_bundle"
    assert result["integrity_valid"] is True
    assert result["details"]["file_count"] == 2

    cases: list[tuple[dict[str, bytes], str]] = []
    cases.append(({"bundle.json": b"{}"}, "обязательных файлов"))
    cases.append(({"bundle.json": b"{", "manifest.json": b"{}"}, "JSON"))
    cases.append(({"bundle.json": b"[]", "manifest.json": b"{}"}, "JSON-объектами"))
    cases.append(
        (
            {"bundle.json": b"{}", "manifest.json": _canonical_json({"files": []})},
            "карту файлов",
        )
    )
    for files, message in cases:
        with pytest.raises(ArtifactVerificationError, match=message):
            _inspect_configuration_zip(files, trusted_fingerprints=set())

    document = b"{}"
    bad_digest = {
        "bundle.json": document,
        "manifest.json": _canonical_json({"files": {"bundle.json": "bad"}}),
    }
    unsafe = {
        "bundle.json": document,
        "manifest.json": _canonical_json({"files": {"../bundle.json": "0" * 64}}),
    }
    duplicate = {
        "bundle.json": document,
        "manifest.json": _canonical_json(
            {"files": {"folder/./file": "0" * 64, "folder/file": "0" * 64}}
        ),
    }
    mismatch_set = configuration_files()
    mismatch_set["extra.bin"] = b"extra"
    mismatch_hash = configuration_files()
    manifest = json.loads(mismatch_hash["manifest.json"])
    manifest["files"]["bundle.json"] = "0" * 64
    mismatch_hash["manifest.json"] = _canonical_json(manifest)
    for files, message in (
        (bad_digest, "некорректный SHA-256"),
        (unsafe, "небезопасный путь"),
        (duplicate, "повторяющийся путь"),
        (mismatch_set, "Состав ZIP"),
        (mismatch_hash, "не совпадает"),
    ):
        with pytest.raises(ArtifactVerificationError, match=message):
            _inspect_configuration_zip(files, trusted_fingerprints=set())


def test_support_manifest_parser_and_archive_errors() -> None:
    """Проверить ASCII manifest support bundle, его состав и SHA-256 payload."""
    digest = hashlib.sha256(b"payload").hexdigest()
    manifest = f"{digest}  report.json\n".encode("ascii")
    valid = {"MANIFEST.sha256": manifest, "report.json": b"payload"}
    result = _inspect_support_zip(valid, trusted_fingerprints=set())
    assert result["artifact_type"] == "support_bundle"
    assert result["details"]["file_count"] == 1

    for raw, message in (
        (b"\xff", "ASCII"),
        (b"short", "Некорректная строка"),
        (f"{'x' * 64}  file\n".encode(), "некорректный digest"),
        (b"\n", "пуст"),
        (f"{digest}  a/./b\n{digest}  a/b\n".encode(), "повторяющийся путь"),
    ):
        with pytest.raises(ArtifactVerificationError, match=message):
            _parse_sha256_manifest(raw)

    with pytest.raises(ArtifactVerificationError, match="нет MANIFEST"):
        _inspect_support_zip({}, trusted_fingerprints=set())
    with pytest.raises(ArtifactVerificationError, match="Состав"):
        _inspect_support_zip(
            {"MANIFEST.sha256": manifest, "other.json": b"payload"},
            trusted_fingerprints=set(),
        )
    with pytest.raises(ArtifactVerificationError, match="не совпадает"):
        _inspect_support_zip(
            {"MANIFEST.sha256": manifest, "report.json": b"changed"},
            trusted_fingerprints=set(),
        )


def test_acceptance_report_success_metadata_and_contract_errors() -> None:
    """Проверить strict JSON wrapper acceptance report и вычисляемые metadata."""
    valid = _inspect_acceptance_json(acceptance_bytes(stages=[{}, {}]), trusted_fingerprints=set())
    assert valid["artifact_type"] == "pilot_acceptance_report"
    assert valid["details"]["program_id"] == "program-1"
    assert valid["details"]["stage_count"] == 2
    unusual = _inspect_acceptance_json(
        acceptance_bytes(program="not-object", stages="not-list"), trusted_fingerprints=set()
    )
    assert unusual["details"]["program_id"] is None
    assert unusual["details"]["program_status"] is None
    assert unusual["details"]["stage_count"] == 0

    with pytest.raises(ArtifactVerificationError, match="повреждён"):
        _inspect_acceptance_json(b"{", trusted_fingerprints=set())
    with pytest.raises(ArtifactVerificationError, match="объектом"):
        _inspect_acceptance_json(b"[]", trusted_fingerprints=set())
    with pytest.raises(ArtifactVerificationError, match="неподдерживаемые поля"):
        _inspect_acceptance_json(b'{"unexpected":true}', trusted_fingerprints=set())
    with pytest.raises(ArtifactVerificationError, match="payload или manifest"):
        _inspect_acceptance_json(b'{"payload":{},"manifest":[]}', trusted_fingerprints=set())

    wrapper = json.loads(acceptance_bytes())
    mutations: list[tuple[Any, str]] = [
        (lambda item: item["manifest"].update(extra=True), "структуру"),
        (lambda item: item["manifest"].update(algorithm="MD5"), "SHA-256"),
        (lambda item: item["manifest"].update(generated_at="other"), "Время"),
        (lambda item: item["manifest"].update(payload_sha256="bad"), "payload"),
        (lambda item: item.update(signature=[]), "JSON-объектом"),
    ]
    for mutate, message in mutations:
        changed = json.loads(json.dumps(wrapper))
        mutate(changed)
        with pytest.raises(ArtifactVerificationError, match=message):
            _inspect_acceptance_json(_canonical_json(changed), trusted_fingerprints=set())


def test_inspect_artifact_dispatch_limits_and_result_metadata() -> None:
    """Проверить dispatch форматов, верхний лимит и итоговые digest/size поля."""
    with pytest.raises(ArtifactVerificationError, match="пуст"):
        inspect_artifact(b"")
    with pytest.raises(ArtifactVerificationError, match="размер"):
        inspect_artifact(b"12", max_bytes=1)
    with pytest.raises(ArtifactVerificationError, match="не распознан"):
        inspect_artifact(zip_bytes([("unknown.txt", b"data")]))

    acceptance = acceptance_bytes()
    result = inspect_artifact(acceptance, trusted_fingerprints=["  ABC  ", ""])
    assert result["artifact_type"] == "pilot_acceptance_report"
    assert result["sha256"] == hashlib.sha256(acceptance).hexdigest()
    assert result["size_bytes"] == len(acceptance)

    config = configuration_files()
    config_zip = zip_bytes(list(config.items()))
    support_manifest = f"{hashlib.sha256(b'x').hexdigest()}  report.txt\n".encode()
    support_zip = zip_bytes([("MANIFEST.sha256", support_manifest), ("report.txt", b"x")])
    assert inspect_artifact(config_zip)["artifact_type"] == "configuration_bundle"
    assert inspect_artifact(support_zip)["artifact_type"] == "support_bundle"
