from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from copy import deepcopy
from typing import Any

import pytest

from app.config import Settings
from app.services import config_bundles
from app.services.config_bundles import (
    BUNDLE_PRODUCT,
    BUNDLE_SCHEMA_VERSION,
    BundleSecurityError,
    ConfigurationBundleError,
    _assert_no_secret_fields,
    _canonical_json,
    _validate_document_shape,
    _validate_zip_path,
    parse_bundle,
)


def minimal_document() -> dict[str, Any]:
    """Создать минимальный безопасный bundle.json."""

    return {
        "product": BUNDLE_PRODUCT,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_product_version": "2.5.0",
        "exported_at": "2026-08-10T00:00:00+00:00",
        "safety_contract": {
            "contains_secrets": False,
            "credential_fields_included": False,
            "free_text_review_required": True,
        },
        "organization_reference": {"name": "Test"},
        "entities": {},
    }


def minimal_manifest(document: dict[str, Any]) -> dict[str, Any]:
    """Создать manifest с корректным SHA-256 bundle.json."""

    return {
        "product": BUNDLE_PRODUCT,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_product_version": "2.5.0",
        "files": {"bundle.json": hashlib.sha256(_canonical_json(document)).hexdigest()},
        "contains_secrets": False,
        "credential_fields_included": False,
        "free_text_review_required": True,
        "entity_counts": {},
    }


def bundle_zip(
    document: dict[str, Any],
    manifest: dict[str, Any],
    *,
    extras: dict[str, bytes] | None = None,
    signature: bytes | None = None,
) -> bytes:
    """Упаковать заданные JSON и дополнительные файлы без автоматических исправлений."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bundle.json", _canonical_json(document))
        archive.writestr("manifest.json", _canonical_json(manifest))
        for name, payload in (extras or {}).items():
            archive.writestr(name, payload)
        if signature is not None:
            archive.writestr("SIGNATURE.json", signature)
    return buffer.getvalue()


def test_bundle_security_helpers_reject_paths_and_secrets() -> None:
    """Проверить path traversal, абсолютные пути и секреты на любой глубине."""

    assert _validate_zip_path("media/id/file.png") == "media/id/file.png"
    for unsafe in ("../escape", "folder/../escape", "/absolute", "\\absolute"):
        with pytest.raises(BundleSecurityError):
            _validate_zip_path(unsafe)

    _assert_no_secret_fields({"safe": [{"value": 1}]})
    for payload in (
        {"bot_token": "secret"},
        {"nested": [{"database_password": "secret"}]},
        {"nested": {"client_secret": "secret"}},
    ):
        with pytest.raises(BundleSecurityError, match="секретное поле"):
            _assert_no_secret_fields(payload)


def test_document_shape_normalizes_all_sections() -> None:
    """Проверить заполнение отсутствующих поддерживаемых разделов пустыми массивами."""

    normalized = _validate_document_shape(minimal_document())
    assert set(normalized) == config_bundles.SUPPORTED_ENTITY_KEYS
    assert all(value == [] for value in normalized.values())


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda doc: doc.update(safety_contract=None), "отсутствие credential"),
        (
            lambda doc: doc["safety_contract"].update(contains_secrets=True),
            "отсутствие credential",
        ),
        (
            lambda doc: doc["safety_contract"].update(credential_fields_included=True),
            "credential-поля",
        ),
        (
            lambda doc: doc["safety_contract"].update(free_text_review_required=False),
            "свободного текста",
        ),
        (lambda doc: doc.update(entities=None), "отсутствует entities"),
        (lambda doc: doc["entities"].update(unknown=[]), "неподдерживаемые разделы"),
        (lambda doc: doc["entities"].update(connections={}), "должен быть массивом"),
        (lambda doc: doc["entities"].update(connections=["bad"]), "должна быть объектом"),
        (lambda doc: doc["entities"].update(connections=[{"ref": ""}]), "Некорректный ref"),
        (
            lambda doc: doc["entities"].update(connections=[{"ref": "same"}, {"ref": "same"}]),
            "Повторяющийся ref",
        ),
    ],
)
def test_document_shape_rejects_invalid_contracts(mutate: Any, expected: str) -> None:
    """Проверить строгую структуру safety contract, entities и refs."""

    document = minimal_document()
    mutate(document)
    with pytest.raises(ConfigurationBundleError, match=expected):
        _validate_document_shape(document)


def test_document_shape_enforces_collection_and_total_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить per-section и суммарный лимит сущностей без больших аллокаций."""

    monkeypatch.setattr(config_bundles, "MAX_ENTITY_COLLECTION", 1)
    document = minimal_document()
    document["entities"] = {"connections": [{"ref": "1"}, {"ref": "2"}]}
    with pytest.raises(BundleSecurityError, match="слишком много записей"):
        _validate_document_shape(document)

    monkeypatch.setattr(config_bundles, "MAX_ENTITY_COLLECTION", 10)
    monkeypatch.setattr(config_bundles, "MAX_BUNDLE_ENTITIES", 1)
    document["entities"] = {
        "connections": [{"ref": "1"}],
        "destinations": [{"ref": "2"}],
    }
    with pytest.raises(BundleSecurityError, match="слишком много сущностей"):
        _validate_document_shape(document)


def test_parse_bundle_accepts_minimal_valid_archive(settings: Settings) -> None:
    """Проверить успешный разбор минимального детерминированного bundle."""

    document = minimal_document()
    manifest = minimal_manifest(document)
    parsed, files, parsed_manifest, signature = parse_bundle(
        bundle_zip(document, manifest), settings=settings
    )
    assert parsed["product"] == BUNDLE_PRODUCT
    assert set(parsed["entities"]) == config_bundles.SUPPORTED_ENTITY_KEYS
    assert files == {}
    assert parsed_manifest == manifest
    assert signature is None


def test_parse_bundle_basic_container_errors(settings: Settings) -> None:
    """Проверить empty/size/type, missing files, traversal и case-insensitive duplicates."""

    with pytest.raises(ConfigurationBundleError, match="пустой"):
        parse_bundle(b"", settings=settings)
    tiny_limit = settings.model_copy(update={"max_export_bytes": 2})
    with pytest.raises(ConfigurationBundleError, match="размер"):
        parse_bundle(b"123", settings=tiny_limit)
    with pytest.raises(ConfigurationBundleError, match="ZIP"):
        parse_bundle(b"not-a-zip", settings=settings)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle.json", b"{}")
    with pytest.raises(ConfigurationBundleError, match="отсутствует"):
        parse_bundle(buffer.getvalue(), settings=settings)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../bundle.json", b"{}")
        archive.writestr("manifest.json", b"{}")
    with pytest.raises(BundleSecurityError, match="небезопасный путь"):
        parse_bundle(buffer.getvalue(), settings=settings)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle.json", b"{}")
        archive.writestr("BUNDLE.JSON", b"{}")
        archive.writestr("manifest.json", b"{}")
    with pytest.raises(BundleSecurityError, match="повторяющиеся имена"):
        parse_bundle(buffer.getvalue(), settings=settings)


def test_parse_bundle_signature_and_json_errors(settings: Settings) -> None:
    """Проверить malformed/non-object signature и повреждённые основные JSON."""

    document = minimal_document()
    manifest = minimal_manifest(document)
    with pytest.raises(BundleSecurityError, match="подписи повреждён"):
        parse_bundle(bundle_zip(document, manifest, signature=b"{"), settings=settings)
    with pytest.raises(BundleSecurityError, match="JSON-объект"):
        parse_bundle(bundle_zip(document, manifest, signature=b"[]"), settings=settings)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle.json", b"{")
        archive.writestr("manifest.json", b"{}")
    with pytest.raises(ConfigurationBundleError, match="JSON"):
        parse_bundle(buffer.getvalue(), settings=settings)


@pytest.mark.parametrize(
    "target,value,expected",
    [
        ("document", {"product": "Other"}, "другим продуктом"),
        ("manifest", {"product": "Other"}, "другим продуктом"),
        ("document", {"unexpected": True}, "неподдерживаемые поля"),
        ("manifest", {"unexpected": True}, "неподдерживаемые поля"),
        ("document", {"schema_version": 999}, "Версия схемы"),
        ("manifest", {"contains_secrets": True}, "отсутствие credential"),
        ("manifest", {"credential_fields_included": True}, "credential-поля"),
        ("manifest", {"free_text_review_required": False}, "свободного текста"),
        ("manifest", {"files": []}, "список файлов"),
    ],
)
def test_parse_bundle_rejects_document_and_manifest_contracts(
    settings: Settings, target: str, value: dict[str, Any], expected: str
) -> None:
    """Проверить product/schema/allowlist/safety поля bundle и manifest."""

    document = minimal_document()
    manifest = minimal_manifest(document)
    selected = document if target == "document" else manifest
    selected.update(value)
    if target == "document" and "unexpected" not in value:
        manifest = minimal_manifest(document)
    with pytest.raises(ConfigurationBundleError, match=expected):
        parse_bundle(bundle_zip(document, manifest), settings=settings)


def test_parse_bundle_rejects_hash_and_file_set_mismatches(settings: Settings) -> None:
    """Проверить формат digest, bundle hash, missing, extra и modified archive files."""

    document = minimal_document()
    manifest = minimal_manifest(document)

    invalid_digest = deepcopy(manifest)
    invalid_digest["files"]["bundle.json"] = "bad"
    with pytest.raises(BundleSecurityError, match="контрольную сумму"):
        parse_bundle(bundle_zip(document, invalid_digest), settings=settings)

    wrong_bundle = deepcopy(manifest)
    wrong_bundle["files"]["bundle.json"] = "0" * 64
    with pytest.raises(BundleSecurityError, match="bundle.json не совпадает"):
        parse_bundle(bundle_zip(document, wrong_bundle), settings=settings)

    declared_missing = deepcopy(manifest)
    declared_missing["files"]["media/missing.bin"] = hashlib.sha256(b"missing").hexdigest()
    with pytest.raises(BundleSecurityError, match="отсутствуют заявленные"):
        parse_bundle(bundle_zip(document, declared_missing), settings=settings)

    with pytest.raises(BundleSecurityError, match="незаявленные"):
        parse_bundle(
            bundle_zip(document, manifest, extras={"media/extra.bin": b"extra"}),
            settings=settings,
        )

    modified = deepcopy(manifest)
    modified["files"]["media/file.bin"] = hashlib.sha256(b"expected").hexdigest()
    with pytest.raises(BundleSecurityError, match="файла media/file.bin"):
        parse_bundle(
            bundle_zip(document, modified, extras={"media/file.bin": b"modified"}),
            settings=settings,
        )


def test_parse_bundle_rejects_symlink(settings: Settings) -> None:
    """Проверить запрет Unix symlink entry внутри ZIP."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle.json", b"{}")
        archive.writestr("manifest.json", b"{}")
        info = zipfile.ZipInfo("media/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")
    with pytest.raises(BundleSecurityError, match="Символические ссылки"):
        parse_bundle(buffer.getvalue(), settings=settings)
