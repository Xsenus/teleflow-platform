from __future__ import annotations

import hashlib
from datetime import UTC, time
from types import SimpleNamespace
from typing import Any

import pytest

from app.enums import ArtifactSignatureStatus
from app.models import MediaAsset
from app.services import config_bundles
from app.services.antivirus import AntivirusUnavailable
from app.services.artifact_signing import ArtifactSigningError
from app.services.config_bundles import (
    BundleSecurityError,
    ConfigurationBundleError,
    _build_document,
    _parse_datetime,
    _parse_time,
    _time_text,
    _unique_name,
    _verify_bundle_signature,
    apply_bundle,
    create_export_bundle,
    parse_bundle,
    preview_bundle,
)
from app.services.media_validation import MediaValidationError
from tests.test_config_bundle_security import bundle_zip, minimal_document, minimal_manifest


class FakeSession:
    """Предоставить минимальный SQLAlchemy Session-контракт для bundle unit-тестов."""

    def __init__(self, scalar_values: list[object | None] | None = None) -> None:
        """Сохранить последовательность scalar-ответов и добавленные модели."""
        self.scalar_values = list(scalar_values or [])
        self.added: list[object] = []
        self.flush_calls = 0

    def scalar(self, _statement: object) -> object | None:
        """Вернуть следующий подготовленный scalar-ответ."""
        return self.scalar_values.pop(0) if self.scalar_values else None

    def scalars(self, _statement: object) -> SimpleNamespace:
        """Вернуть пустую коллекцию для bulk-запросов export builder."""
        return SimpleNamespace(all=lambda: [])

    def add(self, item: object) -> None:
        """Запомнить модель и назначить тестовый id до flush."""
        if getattr(item, "id", None) is None:
            item.id = f"generated-{len(self.added) + 1}"  # type: ignore[attr-defined]
        self.added.append(item)

    def flush(self) -> None:
        """Зафиксировать вызов flush."""
        self.flush_calls += 1


class MemoryStorage:
    """Хранить bundle/media bytes в памяти и фиксировать cleanup."""

    def __init__(self, reads: dict[str, bytes] | None = None) -> None:
        """Инициализировать доступные входные файлы."""
        self.reads = dict(reads or {})
        self.written: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def read_bytes(self, key: str) -> bytes:
        """Прочитать файл либо сообщить о его отсутствии."""
        if key not in self.reads:
            raise FileNotFoundError(key)
        return self.reads[key]

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        """Сохранить bytes и игнорировать MIME только в unit double."""
        assert content_type
        self.written[key] = data

    def delete(self, key: str) -> None:
        """Зафиксировать удаление временного объекта."""
        self.deleted.append(key)
        self.written.pop(key, None)


def export_settings(**overrides: Any) -> SimpleNamespace:
    """Создать минимальные настройки export/parser."""
    values = {
        "version": "2.5.0",
        "artifact_signature_policy": "optional",
        "max_export_bytes": 10_000_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_bundle_time_helpers_normalize_naive_and_short_values() -> None:
    """Проверить datetime/time сериализацию, UTC normalization и оба формата времени."""
    parsed = _parse_datetime("2026-08-10T05:00:00")
    assert parsed is not None and parsed.tzinfo == UTC
    assert _parse_datetime(None) is None
    assert _time_text(time(9, 30)) == "09:30:00"
    assert _time_text(42) == "42"
    assert _parse_time("09:30") == time(9, 30)
    assert _parse_time("09:30:15") == time(9, 30, 15)
    assert _parse_time(None) is None


def test_build_document_handles_missing_and_corrupted_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить пропуск отсутствующего media payload и fail-closed SHA mismatch."""
    asset = SimpleNamespace(
        id="media-id",
        storage_key="media/key",
        relative_path="media/key",
        original_name="picture.png",
        content_type="image/png",
        size_bytes=4,
        sha256=hashlib.sha256(b"good").hexdigest(),
    )
    monkeypatch.setattr(
        config_bundles,
        "_queries",
        lambda _db, model, _organization_id: [asset] if model is MediaAsset else [],
    )
    organization = SimpleNamespace(
        id="organization-id",
        name="Org",
        slug="org",
        timezone_name="UTC",
        retention_days=30,
        ai_enabled=False,
        require_distinct_campaign_approver=True,
        high_risk_destination_threshold=10,
        high_risk_required_approvals=2,
        approval_request_ttl_hours=24,
    )
    document, media_files = _build_document(
        FakeSession(),  # type: ignore[arg-type]
        organization=organization,  # type: ignore[arg-type]
        settings=export_settings(),  # type: ignore[arg-type]
        include_media=True,
        storage=MemoryStorage(),  # type: ignore[arg-type]
    )
    assert media_files == {}
    assert document["entities"]["media"][0]["archive_path"] is None

    with pytest.raises(ConfigurationBundleError, match="SHA-256"):
        _build_document(
            FakeSession(),  # type: ignore[arg-type]
            organization=organization,  # type: ignore[arg-type]
            settings=export_settings(),  # type: ignore[arg-type]
            include_media=True,
            storage=MemoryStorage({"media/key": b"bad"}),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("key", "policy", "expected"),
    [
        (SimpleNamespace(trusted_for_import=True), "optional", "sign failed"),
        (None, "require_trusted", "требуется активный"),
        (None, "optional", "лимит экспорта"),
    ],
)
def test_export_bundle_reports_signing_and_size_failures(
    monkeypatch: pytest.MonkeyPatch,
    key: object | None,
    policy: str,
    expected: str,
) -> None:
    """Проверить controlled errors локальной подписи, обязательного ключа и размера ZIP."""
    document = minimal_document()
    monkeypatch.setattr(config_bundles, "_build_document", lambda *_args, **_kwargs: (document, {}))
    monkeypatch.setattr(config_bundles, "get_default_signing_key", lambda *_args, **_kwargs: key)
    if key is not None:
        monkeypatch.setattr(
            config_bundles,
            "sign_bytes",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ArtifactSigningError("sign failed")),
        )
    max_bytes = 1 if expected == "лимит экспорта" else 10_000_000
    with pytest.raises(ConfigurationBundleError, match=expected):
        create_export_bundle(
            FakeSession(),  # type: ignore[arg-type]
            organization=SimpleNamespace(id="organization-id", slug="org"),  # type: ignore[arg-type]
            created_by=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=export_settings(artifact_signature_policy=policy, max_export_bytes=max_bytes),  # type: ignore[arg-type]
            storage=MemoryStorage(),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            include_media=False,
        )


class FakeZipInfo:
    """Описать управляемую ZIP entry для parser error-path тестов."""

    def __init__(
        self,
        filename: str,
        *,
        size: int = 1,
        encrypted: bool = False,
        directory: bool = False,
    ) -> None:
        """Сохранить ZIP metadata без реального архива."""
        self.filename = filename
        self.file_size = size
        self.flag_bits = 1 if encrypted else 0
        self.external_attr = 0
        self._directory = directory

    def is_dir(self) -> bool:
        """Вернуть признак directory entry."""
        return self._directory


class FakeArchive:
    """Имитировать ZipFile с заданными entries и ошибкой чтения."""

    def __init__(
        self,
        infos: list[FakeZipInfo],
        *,
        read_error: Exception | None = None,
    ) -> None:
        """Сохранить entries и необязательную ошибку read."""
        self.infos = infos
        self.read_error = read_error

    def __enter__(self) -> FakeArchive:
        """Открыть fake archive context."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Закрыть fake archive context без действий."""

    def infolist(self) -> list[FakeZipInfo]:
        """Вернуть заданные ZIP entries."""
        return self.infos

    def read(self, _info: FakeZipInfo) -> bytes:
        """Вернуть payload либо выбросить заданную ошибку."""
        if self.read_error is not None:
            raise self.read_error
        return b"x"


def test_parse_bundle_handles_bad_zip_and_non_object_json(settings: Any) -> None:
    """Проверить damaged central directory и JSON roots другого типа."""
    with pytest.raises(ConfigurationBundleError, match="повреждён"):
        parse_bundle(b"PK\x03\x04broken", settings=settings)
    document = minimal_document()
    manifest = minimal_manifest(document)
    archive = bundle_zip([], manifest)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationBundleError, match="JSON-объекты"):
        parse_bundle(archive, settings=settings)


@pytest.mark.parametrize(
    ("archive", "max_bytes", "message"),
    [
        (FakeArchive([FakeZipInfo(str(index)) for index in range(2001)]), 10_000, "слишком много"),
        (FakeArchive([FakeZipInfo("secret", encrypted=True)]), 10_000, "Зашифрованные"),
        (FakeArchive([FakeZipInfo("folder/", directory=True)]), 10_000, "отсутствует"),
        (FakeArchive([FakeZipInfo("large", size=100)]), 10, "Распакованный"),
        (FakeArchive([FakeZipInfo("broken")], read_error=OSError("read")), 10_000, "распаковать"),
    ],
)
def test_parse_bundle_enforces_zip_metadata_limits(
    monkeypatch: pytest.MonkeyPatch,
    archive: FakeArchive,
    max_bytes: int,
    message: str,
) -> None:
    """Проверить file-count, encryption, directory, expanded-size и read-error guards."""
    monkeypatch.setattr(config_bundles.zipfile, "ZipFile", lambda *_args, **_kwargs: archive)
    with pytest.raises(ConfigurationBundleError, match=message):
        parse_bundle(b"PK\x03\x04x", settings=export_settings(max_export_bytes=max_bytes))  # type: ignore[arg-type]


def test_unique_name_covers_skip_rename_and_exhaustion() -> None:
    """Проверить отсутствие конфликта, skip, rename и исчерпание suffix namespace."""
    model = config_bundles.TelegramConnection
    assert _unique_name(FakeSession(), model, "org", "Name", mode="rename") == (
        "Name",
        None,
        False,
    )
    existing = object()
    assert _unique_name(FakeSession([existing]), model, "org", "Name", mode="skip") == (
        "Name",
        existing,
        False,
    )
    assert _unique_name(FakeSession([existing, None]), model, "org", "Name", mode="rename") == (
        "Name (импорт 2)",
        None,
        True,
    )
    with pytest.raises(ConfigurationBundleError, match="уникальное имя"):
        _unique_name(FakeSession([existing] * 1000), model, "org", "Name", mode="rename")


@pytest.mark.parametrize(
    ("status", "warning"),
    [
        (ArtifactSignatureStatus.UNSIGNED, "не подписан"),
        (ArtifactSignatureStatus.VALID_UNTRUSTED, "не добавлен в доверенные"),
    ],
)
def test_preview_bundle_reports_conflicts_and_signature_warnings(
    monkeypatch: pytest.MonkeyPatch,
    status: ArtifactSignatureStatus,
    warning: str,
) -> None:
    """Проверить name/chat conflicts, dangling destination и media/signature warnings."""
    document = minimal_document()
    document["entities"] = {key: [] for key in config_bundles.SUPPORTED_ENTITY_KEYS}
    document["entities"]["connections"] = [{"ref": "c1", "name": "Existing"}]
    document["entities"]["destinations"] = [
        {"title": "No chat", "telegram_chat_id": None},
        {"title": "No source", "telegram_chat_id": 1, "connection_ref": "missing"},
        {"title": "Existing chat", "telegram_chat_id": 2, "connection_ref": "c1"},
    ]
    document["entities"]["media"] = [{"archive_path": "media/missing.png"}]
    monkeypatch.setattr(
        config_bundles,
        "parse_bundle",
        lambda *_args, **_kwargs: (document, {}, {"manifest": True}, None),
    )
    result_object = SimpleNamespace(status=status, to_dict=lambda: {"status": status.value})
    monkeypatch.setattr(
        config_bundles, "_verify_bundle_signature", lambda *_args, **_kwargs: result_object
    )
    existing_connection = SimpleNamespace(id="connection-id")
    db = FakeSession([object(), existing_connection, object()])
    result = preview_bundle(
        db,  # type: ignore[arg-type]
        organization_id="organization-id",
        data=b"archive",
        settings=export_settings(),  # type: ignore[arg-type]
    )
    assert {item["reason"] for item in result["conflicts"]} == {"name_exists", "chat_exists"}
    assert any(warning in item for item in result["warnings"])
    assert any("Медиафайлы" in item for item in result["warnings"])


def import_settings(**overrides: Any) -> SimpleNamespace:
    """Создать минимальные настройки безопасного configuration import."""
    values = {
        "artifact_signature_policy": "optional",
        "max_export_bytes": 10_000_000,
        "storage_backend": "local",
        "max_media_bytes": 10_000_000,
        "antivirus_fail_closed": True,
        "user_hard_min_interval_seconds": 15,
        "bot_hard_min_interval_seconds": 1,
        "global_hard_daily_cap": 1000,
        "default_destination_cooldown_minutes": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def empty_entities() -> dict[str, list[dict[str, Any]]]:
    """Создать полный пустой набор поддерживаемых import-разделов."""
    return {key: [] for key in config_bundles.SUPPORTED_ENTITY_KEYS}


def verification(status: ArtifactSignatureStatus) -> SimpleNamespace:
    """Создать результат проверки подписи configuration bundle."""
    return SimpleNamespace(
        status=status,
        fingerprint="fingerprint",
        to_dict=lambda: {"status": status.value},
    )


def patch_import(
    monkeypatch: pytest.MonkeyPatch,
    entities: dict[str, list[dict[str, Any]]],
    *,
    files: dict[str, bytes] | None = None,
    status: ArtifactSignatureStatus = ArtifactSignatureStatus.UNSIGNED,
) -> None:
    """Подменить parse/signature этапы, сохранив production apply algorithm."""
    document = minimal_document()
    document["entities"] = entities
    monkeypatch.setattr(
        config_bundles,
        "parse_bundle",
        lambda *_args, **_kwargs: (document, files or {}, {"manifest": True}, None),
    )
    monkeypatch.setattr(
        config_bundles,
        "_verify_bundle_signature",
        lambda *_args, **_kwargs: verification(status),
    )


def test_verify_bundle_signature_maps_policy_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить преобразование signature-policy ошибки в безопасную bundle-ошибку."""
    monkeypatch.setattr(config_bundles, "verify_signature", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        config_bundles,
        "enforce_signature_policy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArtifactSigningError("policy failed")),
    )
    with pytest.raises(BundleSecurityError, match="policy failed"):
        _verify_bundle_signature(
            FakeSession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            manifest={},
            signature_envelope=None,
            settings=export_settings(),  # type: ignore[arg-type]
        )


def test_apply_bundle_creates_every_supported_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить dependency order, reference maps и safe defaults всех import-сущностей."""
    entities = empty_entities()
    entities.update(
        {
            "connections": [{"ref": "c1", "name": "Connection", "kind": "bot"}],
            "destinations": [
                {
                    "ref": "d1",
                    "connection_ref": "c1",
                    "telegram_chat_id": -1001,
                    "title": "Destination",
                    "kind": "group",
                    "permission_reference": {"note": "Owner allowed", "rules_url": "https://x"},
                    "allowed_start_time": "09:00",
                    "allowed_end_time": "18:00:00",
                }
            ],
            "templates": [
                {"ref": "t1", "name": "Template", "body": "Message", "parse_mode": "plain"}
            ],
            "campaigns": [
                {
                    "ref": "campaign1",
                    "name": "Campaign",
                    "connection_ref": "c1",
                    "template_ref": "t1",
                    "schedule_at": "2026-08-10T12:00:00Z",
                    "schedule_type": "once",
                    "rollout_mode": "staged",
                    "destinations": [
                        {"destination_ref": "missing", "position": 0},
                        {"destination_ref": "d1", "position": 1},
                    ],
                }
            ],
            "automation_flows": [{"ref": "f1", "name": "Flow", "definition": {"steps": []}}],
            "ai_providers": [{"ref": "p1", "name": "Provider", "kind": "rule_based"}],
            "automation_policies": [
                {
                    "ref": "policy1",
                    "name": "Policy",
                    "connection_ref": "c1",
                    "provider_ref": "p1",
                    "flow_ref": "f1",
                }
            ],
            "knowledge_articles": [{"ref": "k1", "title": "Article", "content": "Knowledge"}],
            "integrations": [{"ref": "i1", "name": "Integration", "kind": "webhook"}],
            "blackouts": [
                {"ref": "b1", "scope": "organization", "kind": "one_time"},
                {
                    "ref": "b2",
                    "scope": "connection",
                    "connection_ref": "c1",
                    "kind": "one_time",
                },
                {
                    "ref": "b3",
                    "scope": "destination",
                    "destination_ref": "d1",
                    "kind": "one_time",
                },
            ],
        }
    )
    patch_import(monkeypatch, entities)
    monkeypatch.setattr(
        config_bundles,
        "_unique_name",
        lambda _db, _model, _organization_id, base, *, mode: (base, None, False),
    )
    db = FakeSession()
    bundle, created, skipped, renamed, warnings = apply_bundle(
        db,  # type: ignore[arg-type]
        organization_id="organization-id",
        data=b"archive",
        settings=import_settings(),  # type: ignore[arg-type]
        storage=MemoryStorage(),  # type: ignore[arg-type]
        user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
        conflict_mode="rename",
    )
    assert created == {
        "ai_providers": 1,
        "automation_flows": 1,
        "automation_policies": 1,
        "blackouts": 3,
        "campaigns": 1,
        "connections": 1,
        "destinations": 1,
        "integrations": 1,
        "knowledge_articles": 1,
        "media": 0,
        "templates": 1,
    }
    assert all(value == 0 for value in skipped.values())
    assert all(value == 0 for value in renamed.values())
    assert bundle.status.value == "imported"
    assert any("без Ed25519" in item for item in warnings)


def test_apply_bundle_covers_existing_and_dangling_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить skip mappings для existing media/names/chat и dangling foreign refs."""
    entities = empty_entities()
    entities.update(
        {
            "media": [{"ref": "m1", "sha256": "a" * 64}],
            "connections": [{"ref": "c1", "name": "Existing connection"}],
            "destinations": [
                {
                    "ref": "d1",
                    "connection_ref": "c1",
                    "telegram_chat_id": -1001,
                    "title": "Existing destination",
                },
                {"ref": "d2", "connection_ref": "missing", "title": "Dangling"},
            ],
            "templates": [{"ref": "t1", "name": "Existing template"}],
            "campaigns": [
                {
                    "ref": "campaign1",
                    "name": "Existing campaign",
                    "connection_ref": "c1",
                    "template_ref": "t1",
                },
                {"ref": "campaign2", "name": "Dangling campaign"},
            ],
            "automation_flows": [{"ref": "f1", "name": "Existing flow"}],
            "ai_providers": [{"ref": "p1", "name": "Existing provider"}],
            "automation_policies": [
                {"ref": "policy1", "connection_ref": "missing"},
                {"ref": "policy2", "connection_ref": "c1", "name": "Existing policy"},
            ],
            "integrations": [{"ref": "i1", "name": "Existing integration"}],
            "blackouts": [
                {"ref": "b1", "scope": "connection", "connection_ref": "missing"},
                {"ref": "b2", "scope": "destination", "destination_ref": "missing"},
            ],
        }
    )
    patch_import(
        monkeypatch,
        entities,
        status=ArtifactSignatureStatus.VALID_UNTRUSTED,
    )
    existing_by_model = {
        config_bundles.TelegramConnection: SimpleNamespace(id="connection-id"),
        config_bundles.MessageTemplate: SimpleNamespace(id="template-id"),
        config_bundles.Campaign: SimpleNamespace(id="campaign-id"),
        config_bundles.AutomationFlow: SimpleNamespace(id="flow-id"),
        config_bundles.AIProviderConfig: SimpleNamespace(id="provider-id"),
        config_bundles.IntegrationEndpoint: SimpleNamespace(id="integration-id"),
        config_bundles.AutomationPolicy: SimpleNamespace(id="policy-id"),
    }
    monkeypatch.setattr(
        config_bundles,
        "_unique_name",
        lambda _db, model, _organization_id, base, *, mode: (
            base,
            existing_by_model.get(model),
            False,
        ),
    )
    db = FakeSession(
        [
            SimpleNamespace(id="media-id"),
            SimpleNamespace(id="destination-id"),
        ]
    )
    _bundle, created, skipped, _renamed, warnings = apply_bundle(
        db,  # type: ignore[arg-type]
        organization_id="organization-id",
        data=b"archive",
        settings=import_settings(),  # type: ignore[arg-type]
        storage=MemoryStorage(),  # type: ignore[arg-type]
        user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
        conflict_mode="skip",
    )
    assert created["knowledge_articles"] == 0
    assert skipped["media"] == 1
    assert skipped["connections"] == 1
    assert skipped["destinations"] == 2
    assert skipped["templates"] == 1
    assert skipped["campaigns"] == 2
    assert skipped["automation_flows"] == 1
    assert skipped["ai_providers"] == 1
    assert skipped["automation_policies"] == 2
    assert skipped["integrations"] == 1
    assert skipped["blackouts"] == 2
    assert any("не отмечен доверенным" in item for item in warnings)


def media_entities(*, archive_path: str | None = "media/file") -> dict[str, list[dict[str, Any]]]:
    """Создать import document с одним media asset."""
    entities = empty_entities()
    entities["media"] = [
        {
            "ref": "m1",
            "archive_path": archive_path,
            "original_name": "no-extension",
            "content_type": "image/png",
            "sha256": hashlib.sha256(b"payload").hexdigest(),
        }
    ]
    return entities


def test_apply_bundle_skips_media_missing_from_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить controlled skip, когда media entry заявлена без payload."""
    patch_import(monkeypatch, media_entities(), files={})
    _bundle, created, skipped, _renamed, warnings = apply_bundle(
        FakeSession(),  # type: ignore[arg-type]
        organization_id="organization-id",
        data=b"archive",
        settings=import_settings(),  # type: ignore[arg-type]
        storage=MemoryStorage(),  # type: ignore[arg-type]
        user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
        conflict_mode="skip",
    )
    assert created["media"] == 0
    assert skipped["media"] == 1
    assert any("файл не включён" in item for item in warnings)


@pytest.mark.parametrize(
    ("failure", "settings", "message"),
    [
        (MediaValidationError("invalid image"), import_settings(), "invalid image"),
        (
            AntivirusUnavailable("scanner offline"),
            import_settings(antivirus_fail_closed=True),
            "Антивирус недоступен",
        ),
    ],
)
def test_apply_bundle_rejects_invalid_or_unscanned_media(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    settings: SimpleNamespace,
    message: str,
) -> None:
    """Проверить fail-closed media validation и antivirus policy."""
    patch_import(monkeypatch, media_entities(), files={"media/file": b"payload"})
    if isinstance(failure, MediaValidationError):
        monkeypatch.setattr(
            config_bundles,
            "validate_media_bytes",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )
    else:
        monkeypatch.setattr(
            config_bundles,
            "validate_media_bytes",
            lambda *_args, **_kwargs: hashlib.sha256(b"payload").hexdigest(),
        )
        monkeypatch.setattr(
            config_bundles,
            "scan_media_bytes",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )
    with pytest.raises(ConfigurationBundleError, match=message):
        apply_bundle(
            FakeSession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            data=b"archive",
            settings=settings,  # type: ignore[arg-type]
            storage=MemoryStorage(),  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            conflict_mode="skip",
        )


def test_apply_bundle_media_fail_open_and_digest_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить antivirus fail-open warning, extension cleanup и independent digest check."""
    entities = media_entities()
    patch_import(monkeypatch, entities, files={"media/file": b"payload"})
    digest = hashlib.sha256(b"payload").hexdigest()
    monkeypatch.setattr(config_bundles, "validate_media_bytes", lambda *_args, **_kwargs: digest)
    monkeypatch.setattr(
        config_bundles,
        "scan_media_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AntivirusUnavailable("offline")),
    )
    _bundle, created, _skipped, _renamed, warnings = apply_bundle(
        FakeSession(),  # type: ignore[arg-type]
        organization_id="organization-id",
        data=b"archive",
        settings=import_settings(antivirus_fail_closed=False),  # type: ignore[arg-type]
        storage=MemoryStorage(),  # type: ignore[arg-type]
        user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
        conflict_mode="skip",
    )
    assert created["media"] == 1
    assert any("fail-open" in item for item in warnings)

    patch_import(monkeypatch, entities, files={"media/file": b"payload"})
    monkeypatch.setattr(config_bundles, "validate_media_bytes", lambda *_args, **_kwargs: "0" * 64)
    monkeypatch.setattr(config_bundles, "scan_media_bytes", lambda *_args, **_kwargs: None)
    with pytest.raises(BundleSecurityError, match="SHA-256"):
        apply_bundle(
            FakeSession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            data=b"archive",
            settings=import_settings(),  # type: ignore[arg-type]
            storage=MemoryStorage(),  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            conflict_mode="skip",
        )


class DeleteFailStorage(MemoryStorage):
    """Имитировать отказ cleanup storage после частично записанного import."""

    def delete(self, key: str) -> None:
        """Выбросить ошибку удаления для проверки сохранения исходного исключения."""
        self.deleted.append(key)
        raise OSError("delete failed")


class MediaAddFailSession(FakeSession):
    """Имитировать неожиданную ошибку БД после записи media payload."""

    def add(self, item: object) -> None:
        """Остановить import на MediaAsset, остальные модели принимать штатно."""
        if isinstance(item, MediaAsset):
            raise RuntimeError("database failed")
        super().add(item)


@pytest.mark.parametrize("storage", [MemoryStorage(), DeleteFailStorage()])
def test_apply_bundle_cleans_media_after_invalid_entity_values(
    monkeypatch: pytest.MonkeyPatch,
    storage: MemoryStorage,
) -> None:
    """Проверить cleanup записанного media при ValueError и сбое самого delete."""
    entities = media_entities()
    entities["connections"] = [{"ref": "c1", "kind": "invalid-kind"}]
    patch_import(monkeypatch, entities, files={"media/file": b"payload"})
    digest = hashlib.sha256(b"payload").hexdigest()
    monkeypatch.setattr(config_bundles, "validate_media_bytes", lambda *_args, **_kwargs: digest)
    monkeypatch.setattr(config_bundles, "scan_media_bytes", lambda *_args, **_kwargs: None)
    with pytest.raises(ConfigurationBundleError, match="недопустимые значения"):
        apply_bundle(
            FakeSession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            data=b"archive",
            settings=import_settings(),  # type: ignore[arg-type]
            storage=storage,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            conflict_mode="skip",
        )
    assert storage.deleted


def test_apply_bundle_preserves_unexpected_error_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить generic cleanup без маскирования исходной ошибки БД."""
    patch_import(monkeypatch, media_entities(), files={"media/file": b"payload"})
    digest = hashlib.sha256(b"payload").hexdigest()
    monkeypatch.setattr(config_bundles, "validate_media_bytes", lambda *_args, **_kwargs: digest)
    monkeypatch.setattr(config_bundles, "scan_media_bytes", lambda *_args, **_kwargs: None)
    storage = DeleteFailStorage()
    with pytest.raises(RuntimeError, match="database failed"):
        apply_bundle(
            MediaAddFailSession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            data=b"archive",
            settings=import_settings(),  # type: ignore[arg-type]
            storage=storage,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            conflict_mode="skip",
        )
    assert storage.deleted


def test_apply_bundle_rejects_unknown_conflict_mode() -> None:
    """Проверить отказ до parse/storage при неизвестном conflict mode."""
    with pytest.raises(ConfigurationBundleError, match="conflict_mode"):
        apply_bundle(
            FakeSession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            data=b"archive",
            settings=import_settings(),  # type: ignore[arg-type]
            storage=MemoryStorage(),  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            conflict_mode="overwrite",
        )
