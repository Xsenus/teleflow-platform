from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from app.enums import ArtifactSignatureStatus
from app.services.artifact_signing import (
    SIGNATURE_FILENAME,
    verify_embedded_signature,
)

MAX_INSPECTION_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_FILES = 5000
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactVerificationError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    """Реализовать внутренний этап canonical json step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _safe_path(raw: str) -> str:
    """Реализовать внутренний этап safe path step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ArtifactVerificationError("Артефакт содержит небезопасный путь")
    normalized = str(path)
    if normalized.startswith(("/", "\\")):
        raise ArtifactVerificationError("Артефакт содержит абсолютный путь")
    return normalized


def _read_zip(data: bytes, *, max_bytes: int) -> dict[str, bytes]:
    """Реализовать внутренний этап read zip step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ArtifactVerificationError("ZIP-архив повреждён") from exc
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise ArtifactVerificationError("В ZIP-архиве слишком много файлов")
        for info in infos:
            name = _safe_path(info.filename)
            folded = name.casefold()
            if folded in seen:
                raise ArtifactVerificationError("ZIP содержит повторяющиеся пути")
            seen.add(folded)
            if info.flag_bits & 0x1:
                raise ArtifactVerificationError("Зашифрованные ZIP entries не поддерживаются")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ArtifactVerificationError("Символические ссылки в ZIP запрещены")
            if info.is_dir():
                continue
            total += info.file_size
            if total > max_bytes:
                raise ArtifactVerificationError("Распакованный артефакт превышает лимит")
            try:
                files[name] = archive.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
                raise ArtifactVerificationError(f"Не удалось прочитать {name}") from exc
    return files


def _signature_envelope(files: dict[str, bytes]) -> dict[str, Any] | None:
    """Реализовать внутренний этап signature envelope step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    raw = files.get(SIGNATURE_FILENAME)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactVerificationError("SIGNATURE.json повреждён") from exc
    if not isinstance(value, dict):
        raise ArtifactVerificationError("SIGNATURE.json должен содержать JSON-объект")
    return value


def _apply_local_trust(
    signature: dict[str, Any], *, trusted_fingerprints: set[str]
) -> dict[str, Any]:
    """Реализовать внутренний этап apply local trust step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    result = dict(signature)
    fingerprint = str(result.get("fingerprint") or "").lower()
    trusted = bool(
        result.get("cryptographically_valid")
        and fingerprint
        and fingerprint in trusted_fingerprints
    )
    result["trusted"] = trusted
    if trusted:
        result["status"] = ArtifactSignatureStatus.VALID_TRUSTED.value
    return result


def _inspect_configuration_zip(
    files: dict[str, bytes], *, trusted_fingerprints: set[str]
) -> dict[str, Any]:
    """Реализовать внутренний этап inspect configuration zip step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    if "bundle.json" not in files or "manifest.json" not in files:
        raise ArtifactVerificationError("В конфигурационном архиве нет обязательных файлов")
    try:
        manifest = json.loads(files["manifest.json"])
        document = json.loads(files["bundle.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactVerificationError("JSON конфигурационного архива повреждён") from exc
    if not isinstance(manifest, dict) or not isinstance(document, dict):
        raise ArtifactVerificationError("Manifest и bundle должны быть JSON-объектами")
    expected = manifest.get("files")
    if not isinstance(expected, dict):
        raise ArtifactVerificationError("Manifest не содержит карту файлов")
    normalized_expected: dict[str, str] = {}
    for raw_name, raw_digest in expected.items():
        name = _safe_path(str(raw_name))
        digest = str(raw_digest).lower()
        if not _HEX_64.fullmatch(digest):
            raise ArtifactVerificationError("Manifest содержит некорректный SHA-256")
        if name in normalized_expected:
            raise ArtifactVerificationError("Manifest содержит повторяющийся путь")
        normalized_expected[name] = digest
    payload_files = {
        name: payload
        for name, payload in files.items()
        if name not in {"manifest.json", SIGNATURE_FILENAME}
    }
    if set(payload_files) != set(normalized_expected):
        raise ArtifactVerificationError("Состав ZIP не совпадает с manifest")
    for name, digest in normalized_expected.items():
        if hashlib.sha256(payload_files[name]).hexdigest() != digest:
            raise ArtifactVerificationError(f"SHA-256 файла {name} не совпадает")
    envelope = _signature_envelope(files)
    verification = verify_embedded_signature(
        data=_canonical_json(manifest),
        envelope=envelope,
        expected_purpose="teleflow.configuration_bundle.manifest.v1",
    ).to_dict()
    return {
        "artifact_type": "configuration_bundle",
        "integrity_valid": True,
        "signature": _apply_local_trust(verification, trusted_fingerprints=trusted_fingerprints),
        "details": {
            "product": manifest.get("product"),
            "schema_version": manifest.get("schema_version"),
            "source_product_version": manifest.get("source_product_version"),
            "file_count": len(normalized_expected),
            "entity_counts": manifest.get("entity_counts") or {},
            "contains_secrets": manifest.get("contains_secrets"),
        },
    }


def _parse_sha256_manifest(raw: bytes) -> dict[str, str]:
    """Вычислить parse sha256 manifest. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ArtifactVerificationError("MANIFEST.sha256 должен быть ASCII") from exc
    result: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        if len(line) < 67 or line[64:66] != "  ":
            raise ArtifactVerificationError(f"Некорректная строка MANIFEST.sha256: {line_number}")
        digest = line[:64].lower()
        name = _safe_path(line[66:])
        if not _HEX_64.fullmatch(digest):
            raise ArtifactVerificationError("MANIFEST.sha256 содержит некорректный digest")
        if name in result:
            raise ArtifactVerificationError("MANIFEST.sha256 содержит повторяющийся путь")
        result[name] = digest
    if not result:
        raise ArtifactVerificationError("MANIFEST.sha256 пуст")
    return result


def _inspect_support_zip(
    files: dict[str, bytes], *, trusted_fingerprints: set[str]
) -> dict[str, Any]:
    """Реализовать внутренний этап inspect support zip step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    manifest_raw = files.get("MANIFEST.sha256")
    if manifest_raw is None:
        raise ArtifactVerificationError("В диагностическом архиве нет MANIFEST.sha256")
    expected = _parse_sha256_manifest(manifest_raw)
    payload_files = {
        name: payload
        for name, payload in files.items()
        if name not in {"MANIFEST.sha256", SIGNATURE_FILENAME}
    }
    if set(payload_files) != set(expected):
        raise ArtifactVerificationError("Состав диагностического ZIP не совпадает с manifest")
    for name, digest in expected.items():
        if hashlib.sha256(payload_files[name]).hexdigest() != digest:
            raise ArtifactVerificationError(f"SHA-256 файла {name} не совпадает")
    envelope = _signature_envelope(files)
    verification = verify_embedded_signature(
        data=manifest_raw,
        envelope=envelope,
        expected_purpose="teleflow.support_bundle.manifest.v1",
    ).to_dict()
    return {
        "artifact_type": "support_bundle",
        "integrity_valid": True,
        "signature": _apply_local_trust(verification, trusted_fingerprints=trusted_fingerprints),
        "details": {"file_count": len(expected)},
    }


def _inspect_acceptance_json(data: bytes, *, trusted_fingerprints: set[str]) -> dict[str, Any]:
    """Реализовать внутренний этап inspect acceptance json step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    try:
        wrapper = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactVerificationError("JSON-артефакт повреждён") from exc
    if not isinstance(wrapper, dict):
        raise ArtifactVerificationError("JSON-артефакт должен быть объектом")
    allowed_wrapper_fields = {"payload", "manifest", "signature"}
    unexpected_wrapper_fields = set(wrapper) - allowed_wrapper_fields
    if unexpected_wrapper_fields:
        raise ArtifactVerificationError(
            "Акт содержит неподдерживаемые поля: " + ", ".join(sorted(unexpected_wrapper_fields))
        )
    payload = wrapper.get("payload")
    manifest = wrapper.get("manifest")
    if not isinstance(payload, dict) or not isinstance(manifest, dict):
        raise ArtifactVerificationError("Акт не содержит payload или manifest")
    expected_manifest_fields = {"algorithm", "payload_sha256", "generated_at"}
    if set(manifest) != expected_manifest_fields:
        raise ArtifactVerificationError("Manifest акта имеет неподдерживаемую структуру")
    if manifest.get("algorithm") != "SHA-256":
        raise ArtifactVerificationError("Акт должен использовать алгоритм SHA-256")
    generated_at = manifest.get("generated_at")
    if not isinstance(generated_at, str) or generated_at != payload.get("generated_at"):
        raise ArtifactVerificationError("Время формирования акта в manifest не совпадает с payload")
    canonical = _canonical_json(payload)
    digest = hashlib.sha256(canonical).hexdigest()
    claimed = str(manifest.get("payload_sha256") or "").lower()
    if not _HEX_64.fullmatch(claimed) or digest != claimed:
        raise ArtifactVerificationError("SHA-256 payload акта не совпадает")
    envelope = wrapper.get("signature")
    if envelope is not None and not isinstance(envelope, dict):
        raise ArtifactVerificationError("Подпись акта должна быть JSON-объектом")
    verification = verify_embedded_signature(
        data=canonical,
        envelope=envelope,
        expected_purpose="teleflow.pilot_acceptance.payload.v1",
    ).to_dict()
    return {
        "artifact_type": "pilot_acceptance_report",
        "integrity_valid": True,
        "signature": _apply_local_trust(verification, trusted_fingerprints=trusted_fingerprints),
        "details": {
            "product": payload.get("product"),
            "report_schema_version": payload.get("report_schema_version"),
            "program_id": (payload.get("program") or {}).get("id")
            if isinstance(payload.get("program"), dict)
            else None,
            "program_status": (payload.get("program") or {}).get("status")
            if isinstance(payload.get("program"), dict)
            else None,
            "stage_count": len(payload.get("stages") or [])
            if isinstance(payload.get("stages"), list)
            else 0,
        },
    }


def inspect_artifact(
    data: bytes,
    *,
    trusted_fingerprints: Iterable[str] = (),
    max_bytes: int = MAX_INSPECTION_BYTES,
) -> dict[str, Any]:
    """Выполнить операцию inspect artifact. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if not data:
        raise ArtifactVerificationError("Артефакт пуст")
    if len(data) > max_bytes:
        raise ArtifactVerificationError("Артефакт превышает допустимый размер")
    trusted = {str(item).strip().lower() for item in trusted_fingerprints if str(item).strip()}
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        files = _read_zip(data, max_bytes=max_bytes)
        if "manifest.json" in files or "bundle.json" in files:
            result = _inspect_configuration_zip(files, trusted_fingerprints=trusted)
        elif "MANIFEST.sha256" in files:
            result = _inspect_support_zip(files, trusted_fingerprints=trusted)
        else:
            raise ArtifactVerificationError("Тип ZIP-артефакта TeleFlow не распознан")
    else:
        result = _inspect_acceptance_json(data, trusted_fingerprints=trusted)
    result["sha256"] = hashlib.sha256(data).hexdigest()
    result["size_bytes"] = len(data)
    return result
