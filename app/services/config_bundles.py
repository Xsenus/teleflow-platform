from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    AIProviderKind,
    ArtifactSignatureStatus,
    BlackoutKind,
    BlackoutScope,
    CampaignStatus,
    ConfigurationBundleKind,
    ConfigurationBundleStatus,
    ConnectionKind,
    ConnectionStatus,
    DestinationKind,
    IntegrationKind,
    ParseMode,
    PermissionStatus,
    RolloutMode,
    ScheduleType,
)
from app.models import (
    AIProviderConfig,
    AutomationFlow,
    AutomationPolicy,
    Campaign,
    CampaignDestination,
    ConfigurationBundle,
    Destination,
    IntegrationEndpoint,
    KnowledgeBaseArticle,
    MediaAsset,
    MessageTemplate,
    Organization,
    PublishingBlackout,
    TelegramConnection,
    User,
    utcnow,
)
from app.services.antivirus import AntivirusUnavailable, MalwareDetected, scan_media_bytes
from app.services.artifact_signing import (
    SIGNATURE_FILENAME,
    ArtifactSigningError,
    SignatureVerification,
    enforce_signature_policy,
    get_default_signing_key,
    sign_bytes,
    verify_signature,
)
from app.services.crypto import SecretCipher
from app.services.media_validation import MediaValidationError, validate_media_bytes
from app.services.storage import StorageService

BUNDLE_SCHEMA_VERSION = 1
BUNDLE_PRODUCT = "TeleFlow Platform"
SUPPORTED_ENTITY_KEYS = {
    "connections",
    "destinations",
    "media",
    "templates",
    "campaigns",
    "blackouts",
    "automation_flows",
    "ai_providers",
    "automation_policies",
    "knowledge_articles",
    "integrations",
}
FORBIDDEN_SECRET_KEYS = {
    "api_hash",
    "api_key",
    "api_key_enc",
    "auth_session",
    "bot_token",
    "cloud_password",
    "config_enc",
    "credentials",
    "credentials_enc",
    "jwt_secret",
    "master_key",
    "password",
    "private_key",
    "secret",
    "secret_key",
    "secret_token",
    "service_account",
    "session",
    "session_enc",
    "string_session",
    "token",
    "totp_secret",
}
MAX_BUNDLE_ENTITIES = 10_000
MAX_ENTITY_COLLECTION = 5_000


class ConfigurationBundleError(RuntimeError):
    pass


class BundleSecurityError(ConfigurationBundleError):
    pass


def _iso(value: datetime | None) -> str | None:
    """Реализовать внутренний этап iso step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    """Реализовать внутренний этап parse datetime step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _time_text(value: Any) -> str | None:
    """Реализовать внутренний этап time text step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _parse_time(value: str | None):
    """Реализовать внутренний этап parse time step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if not value:
        return None
    return (
        datetime.strptime(value[:8], "%H:%M:%S").time()
        if len(value) >= 8
        else datetime.strptime(value, "%H:%M").time()
    )


def _safe_filename(value: str) -> str:
    """Реализовать внутренний этап safe filename step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    name = Path(value or "file").name
    cleaned = re.sub(r"[^A-Za-zА-Яа-я0-9._-]+", "_", name).strip("._")
    return (cleaned or "file")[:180]


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


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    """Реализовать внутренний этап zip write step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def _queries(db: Session, model: Any, organization_id: str) -> list[Any]:
    """Реализовать внутренний этап queries step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return list(
        db.scalars(
            select(model)
            .where(model.organization_id == organization_id)
            .order_by(getattr(model, "created_at", model.id), model.id)
        ).all()
    )


def _build_document(
    db: Session,
    *,
    organization: Organization,
    settings: Settings,
    include_media: bool,
    storage: StorageService,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Реализовать внутренний этап build document step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    organization_id = organization.id
    connections = _queries(db, TelegramConnection, organization_id)
    destinations = _queries(db, Destination, organization_id)
    media = _queries(db, MediaAsset, organization_id)
    templates = _queries(db, MessageTemplate, organization_id)
    campaigns = _queries(db, Campaign, organization_id)
    blackouts = _queries(db, PublishingBlackout, organization_id)
    flows = _queries(db, AutomationFlow, organization_id)
    providers = _queries(db, AIProviderConfig, organization_id)
    policies = _queries(db, AutomationPolicy, organization_id)
    knowledge = _queries(db, KnowledgeBaseArticle, organization_id)
    integrations = _queries(db, IntegrationEndpoint, organization_id)
    campaign_links = list(
        db.scalars(
            select(CampaignDestination)
            .where(CampaignDestination.organization_id == organization_id)
            .order_by(CampaignDestination.campaign_id, CampaignDestination.position)
        ).all()
    )
    links_by_campaign: dict[str, list[CampaignDestination]] = {}
    for link in campaign_links:
        links_by_campaign.setdefault(link.campaign_id, []).append(link)

    media_files: dict[str, bytes] = {}
    media_rows: list[dict[str, Any]] = []
    for asset in media:
        archive_path = None
        if include_media:
            key = asset.storage_key or asset.relative_path
            try:
                data = storage.read_bytes(key)
            except FileNotFoundError:
                data = b""
            if data:
                digest = hashlib.sha256(data).hexdigest()
                if digest != asset.sha256:
                    raise ConfigurationBundleError(
                        f"Медиафайл {asset.original_name} не соответствует сохранённому SHA-256"
                    )
                archive_path = f"media/{asset.id}/{_safe_filename(asset.original_name)}"
                media_files[archive_path] = data
        media_rows.append(
            {
                "ref": asset.id,
                "original_name": asset.original_name,
                "content_type": asset.content_type,
                "size_bytes": asset.size_bytes,
                "sha256": asset.sha256,
                "archive_path": archive_path,
            }
        )

    document: dict[str, Any] = {
        "product": BUNDLE_PRODUCT,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_product_version": settings.version,
        "exported_at": utcnow().isoformat(),
        "safety_contract": {
            "contains_secrets": False,
            "credential_fields_included": False,
            "free_text_review_required": True,
            "connections_import_as_draft": True,
            "destinations_import_disabled_and_unverified": True,
            "campaigns_import_as_draft": True,
            "automations_import_disabled": True,
        },
        "organization_reference": {
            "name": organization.name,
            "slug": organization.slug,
            "timezone_name": organization.timezone_name,
            "retention_days": organization.retention_days,
            "ai_enabled": organization.ai_enabled,
            "governance": {
                "require_distinct_campaign_approver": organization.require_distinct_campaign_approver,
                "high_risk_destination_threshold": organization.high_risk_destination_threshold,
                "high_risk_required_approvals": organization.high_risk_required_approvals,
                "approval_request_ttl_hours": organization.approval_request_ttl_hours,
            },
        },
        "entities": {
            "connections": [
                {
                    "ref": item.id,
                    "name": item.name,
                    "kind": item.kind.value,
                    "min_interval_seconds": item.min_interval_seconds,
                    "daily_cap": item.daily_cap,
                    "destination_cooldown_minutes": item.destination_cooldown_minutes,
                    "require_manual_approval": True,
                    "stop_on_flood": True,
                }
                for item in connections
            ],
            "destinations": [
                {
                    "ref": item.id,
                    "connection_ref": item.connection_id,
                    "telegram_chat_id": item.telegram_chat_id,
                    "username": item.username,
                    "title": item.title,
                    "kind": item.kind.value,
                    "topic_id": item.topic_id,
                    "timezone_name": item.timezone_name,
                    "allowed_weekdays": item.allowed_weekdays,
                    "allowed_start_time": _time_text(item.allowed_start_time),
                    "allowed_end_time": _time_text(item.allowed_end_time),
                    "cooldown_minutes_override": item.cooldown_minutes_override,
                    "permission_reference": {
                        "source_status": item.permission_status.value,
                        "note": item.permission_note,
                        "rules_url": item.rules_url,
                        "reviewed_at": _iso(item.permission_reviewed_at),
                        "expires_at": _iso(item.permission_expires_at),
                    },
                }
                for item in destinations
            ],
            "media": media_rows,
            "templates": [
                {
                    "ref": item.id,
                    "name": item.name,
                    "body": item.body,
                    "parse_mode": item.parse_mode.value,
                    "media_ref": item.media_asset_id,
                    "link_preview": item.link_preview,
                    "revision": item.revision,
                }
                for item in templates
            ],
            "campaigns": [
                {
                    "ref": item.id,
                    "name": item.name,
                    "connection_ref": item.connection_id,
                    "template_ref": item.template_id,
                    "secondary_template_ref": item.secondary_template_id,
                    "secondary_template_weight": item.secondary_template_weight,
                    "schedule_type": item.schedule_type.value,
                    "schedule_at": _iso(item.schedule_at),
                    "timezone_name": item.timezone_name,
                    "weekdays": item.weekdays,
                    "spacing_seconds": item.spacing_seconds,
                    "rollout_mode": item.rollout_mode.value,
                    "rollout_batch_size": item.rollout_batch_size,
                    "rollout_pause_seconds": item.rollout_pause_seconds,
                    "rollout_require_checkpoint": item.rollout_require_checkpoint,
                    "rollout_failure_threshold_percent": item.rollout_failure_threshold_percent,
                    "duplicate_guard_minutes": item.duplicate_guard_minutes,
                    "end_at": _iso(item.end_at),
                    "notes": item.notes,
                    "destinations": [
                        {
                            "destination_ref": link.destination_id,
                            "position": link.position,
                            "custom_body": link.custom_body,
                            "enabled": link.enabled,
                        }
                        for link in links_by_campaign.get(item.id, [])
                    ],
                }
                for item in campaigns
            ],
            "blackouts": [
                {
                    "ref": item.id,
                    "scope": item.scope.value,
                    "kind": item.kind.value,
                    "connection_ref": item.connection_id,
                    "destination_ref": item.destination_id,
                    "title": item.title,
                    "reason": item.reason,
                    "starts_at": _iso(item.starts_at),
                    "ends_at": _iso(item.ends_at),
                    "timezone_name": item.timezone_name,
                    "weekdays": item.weekdays,
                    "start_time": _time_text(item.start_time),
                    "end_time": _time_text(item.end_time),
                }
                for item in blackouts
            ],
            "automation_flows": [
                {
                    "ref": item.id,
                    "name": item.name,
                    "description": item.description,
                    "revision": item.revision,
                    "definition": item.definition,
                }
                for item in flows
            ],
            "ai_providers": [
                {
                    "ref": item.id,
                    "name": item.name,
                    "kind": item.kind.value,
                    "base_url": item.base_url,
                    "model_name": item.model_name,
                    "timeout_seconds": item.timeout_seconds,
                    "max_output_tokens": item.max_output_tokens,
                    "temperature_milli": item.temperature_milli,
                    "data_region": item.data_region,
                    "system_prompt": item.system_prompt,
                    "allowed_models": item.allowed_models,
                }
                for item in providers
            ],
            "automation_policies": [
                {
                    "ref": item.id,
                    "name": item.name,
                    "connection_ref": item.telegram_connection_id,
                    "provider_ref": item.ai_provider_config_id,
                    "flow_ref": item.automation_flow_id,
                    "timezone_name": item.timezone_name,
                    "active_hours": item.active_hours,
                    "allowed_chat_types": item.allowed_chat_types,
                    "consent_notice": item.consent_notice,
                    "fallback_message": item.fallback_message,
                    "max_auto_replies_per_day": item.max_auto_replies_per_day,
                    "require_consent_before_ai": item.require_consent_before_ai,
                    "handoff_keywords": item.handoff_keywords,
                    "stop_words": item.stop_words,
                    "vacancy_detection_rules": item.vacancy_detection_rules,
                }
                for item in policies
            ],
            "knowledge_articles": [
                {
                    "ref": item.id,
                    "title": item.title,
                    "content": item.content,
                    "tags": item.tags,
                    "vacancy_key": item.vacancy_key,
                    "revision": item.revision,
                }
                for item in knowledge
            ],
            "integrations": [
                {
                    "ref": item.id,
                    "name": item.name,
                    "kind": item.kind.value,
                    "event_types": item.event_types,
                }
                for item in integrations
            ],
        },
    }
    return document, media_files


def create_export_bundle(
    db: Session,
    *,
    organization: Organization,
    created_by: User,
    settings: Settings,
    storage: StorageService,
    cipher: SecretCipher,
    include_media: bool,
) -> ConfigurationBundle:
    """Создать export bundle. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    document, media_files = _build_document(
        db,
        organization=organization,
        settings=settings,
        include_media=include_media,
        storage=storage,
    )
    bundle_json = _canonical_json(document)
    file_hashes = {"bundle.json": hashlib.sha256(bundle_json).hexdigest()}
    for path, data in sorted(media_files.items()):
        file_hashes[path] = hashlib.sha256(data).hexdigest()
    manifest = {
        "product": BUNDLE_PRODUCT,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_product_version": settings.version,
        "files": file_hashes,
        "contains_secrets": False,
        "credential_fields_included": False,
        "free_text_review_required": True,
        "entity_counts": {key: len(value) for key, value in document["entities"].items()},
    }
    manifest_json = _canonical_json(manifest)
    signature_envelope: dict[str, Any] | None = None
    signing_key = get_default_signing_key(db, organization_id=organization.id, require_private=True)
    if (
        signing_key is not None
        and settings.artifact_signature_policy == "require_trusted"
        and not signing_key.trusted_for_import
    ):
        raise ConfigurationBundleError("Основной Ed25519-ключ должен быть доверен организацией")
    if signing_key is not None:
        try:
            signature_envelope = sign_bytes(
                manifest_json,
                key=signing_key,
                cipher=cipher,
                purpose="teleflow.configuration_bundle.manifest.v1",
            )
        except ArtifactSigningError as exc:
            raise ConfigurationBundleError(str(exc)) from exc
    elif settings.artifact_signature_policy != "optional":
        raise ConfigurationBundleError(
            "Для экспорта требуется активный основной Ed25519-ключ подписи"
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", allowZip64=False) as archive:
        _zip_write(archive, "bundle.json", bundle_json)
        for path, data in sorted(media_files.items()):
            _zip_write(archive, path, data)
        _zip_write(archive, "manifest.json", manifest_json)
        if signature_envelope is not None:
            _zip_write(archive, SIGNATURE_FILENAME, _canonical_json(signature_envelope))
    data = buffer.getvalue()
    if len(data) > settings.max_export_bytes:
        raise ConfigurationBundleError("Конфигурационный архив превышает лимит экспорта")
    digest = hashlib.sha256(data).hexdigest()
    filename = f"teleflow-config-{organization.slug}-{utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    key = f"configuration-bundles/{organization.id}/{uuid.uuid4().hex}.zip"
    storage.put_bytes(key, data, content_type="application/zip")
    item = ConfigurationBundle(
        organization_id=organization.id,
        kind=ConfigurationBundleKind.EXPORT,
        status=ConfigurationBundleStatus.READY,
        schema_version=BUNDLE_SCHEMA_VERSION,
        source_product_version=settings.version,
        filename=filename,
        storage_key=key,
        sha256=digest,
        size_bytes=len(data),
        include_media=include_media,
        manifest=manifest,
        summary=manifest["entity_counts"],
        signature_status=(
            ArtifactSignatureStatus.VALID_TRUSTED
            if signing_key and signing_key.trusted_for_import
            else ArtifactSignatureStatus.VALID_UNTRUSTED
            if signing_key
            else ArtifactSignatureStatus.UNSIGNED
        ),
        signature_info=signature_envelope or {},
        signer_fingerprint=signing_key.fingerprint if signing_key else None,
        created_by_id=created_by.id,
        created_at=utcnow(),
    )
    db.add(item)
    db.flush()
    return item


def _validate_zip_path(name: str) -> str:
    """Реализовать внутренний этап validate zip path step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BundleSecurityError("Архив содержит небезопасный путь")
    normalized = str(path)
    if normalized.startswith("/") or normalized.startswith("\\"):
        raise BundleSecurityError("Архив содержит абсолютный путь")
    return normalized


def _assert_no_secret_fields(value: Any, *, path: str = "bundle") -> None:
    """Реализовать внутренний этап assert no secret fields step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower()
            if key in FORBIDDEN_SECRET_KEYS or key.endswith(("_password", "_secret", "_token")):
                raise BundleSecurityError(
                    f"Конфигурационный архив содержит запрещённое секретное поле: {path}.{raw_key}"
                )
            _assert_no_secret_fields(nested, path=f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_secret_fields(nested, path=f"{path}[{index}]")


def _validate_document_shape(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Вычислить validate document shape. Канонический ввод обеспечивает детерминированное
    сравнение целостности между процессами.
    """
    safety = document.get("safety_contract")
    if not isinstance(safety, dict) or safety.get("contains_secrets") is not False:
        raise BundleSecurityError("Архив не подтверждает отсутствие credential-полей")
    if safety.get("credential_fields_included") is not False:
        raise BundleSecurityError("Архив может содержать credential-поля")
    if safety.get("free_text_review_required") is not True:
        raise BundleSecurityError("Архив не подтверждает необходимость проверки свободного текста")
    _assert_no_secret_fields(document)

    entities = document.get("entities")
    if not isinstance(entities, dict):
        raise ConfigurationBundleError("В bundle.json отсутствует entities")
    unknown = set(entities) - SUPPORTED_ENTITY_KEYS
    if unknown:
        raise ConfigurationBundleError(
            "Архив содержит неподдерживаемые разделы: " + ", ".join(sorted(unknown))
        )
    total = 0
    normalized: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(SUPPORTED_ENTITY_KEYS):
        value = entities.get(key, [])
        if not isinstance(value, list):
            raise ConfigurationBundleError(f"Раздел entities.{key} должен быть массивом")
        if len(value) > MAX_ENTITY_COLLECTION:
            raise BundleSecurityError(f"В разделе entities.{key} слишком много записей")
        rows: list[dict[str, Any]] = []
        refs: set[str] = set()
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ConfigurationBundleError(
                    f"Запись entities.{key}[{index}] должна быть объектом"
                )
            ref = item.get("ref")
            if ref is not None:
                ref_text = str(ref)
                if not ref_text or len(ref_text) > 200:
                    raise ConfigurationBundleError(f"Некорректный ref в entities.{key}[{index}]")
                if ref_text in refs:
                    raise BundleSecurityError(f"Повторяющийся ref в entities.{key}: {ref_text}")
                refs.add(ref_text)
            rows.append(item)
        total += len(rows)
        normalized[key] = rows
    if total > MAX_BUNDLE_ENTITIES:
        raise BundleSecurityError("В конфигурационном архиве слишком много сущностей")
    return normalized


def parse_bundle(
    data: bytes, *, settings: Settings
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any], dict[str, Any] | None]:
    """Выполнить операцию parse bundle. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if not data:
        raise ConfigurationBundleError("Загружен пустой файл")
    if len(data) > settings.max_export_bytes:
        raise ConfigurationBundleError("Архив превышает допустимый размер")
    if not data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        raise ConfigurationBundleError("Ожидается ZIP-архив TeleFlow")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ConfigurationBundleError("ZIP-архив повреждён") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 2000:
            raise BundleSecurityError("В архиве слишком много файлов")
        total = 0
        files: dict[str, bytes] = {}
        seen_paths: set[str] = set()
        for info in infos:
            name = _validate_zip_path(info.filename)
            canonical_name = name.casefold()
            if canonical_name in seen_paths:
                raise BundleSecurityError("Архив содержит повторяющиеся имена файлов")
            seen_paths.add(canonical_name)
            if info.flag_bits & 0x1:
                raise BundleSecurityError("Зашифрованные файлы внутри ZIP не поддерживаются")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise BundleSecurityError("Символические ссылки в архиве запрещены")
            if info.is_dir():
                continue
            total += info.file_size
            if total > settings.max_export_bytes:
                raise BundleSecurityError("Распакованный архив превышает допустимый размер")
            try:
                files[name] = archive.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
                raise ConfigurationBundleError(
                    f"Не удалось безопасно распаковать файл {name}"
                ) from exc
    if "bundle.json" not in files or "manifest.json" not in files:
        raise ConfigurationBundleError("В архиве отсутствует bundle.json или manifest.json")
    signature_envelope: dict[str, Any] | None = None
    if SIGNATURE_FILENAME in files:
        try:
            signature_value = json.loads(files.pop(SIGNATURE_FILENAME))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleSecurityError("Файл Ed25519-подписи повреждён") from exc
        if not isinstance(signature_value, dict):
            raise BundleSecurityError("Файл Ed25519-подписи должен содержать JSON-объект")
        _assert_no_secret_fields(signature_value, path="signature")
        signature_envelope = signature_value
    try:
        document = json.loads(files.pop("bundle.json"))
        manifest = json.loads(files.pop("manifest.json"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationBundleError("JSON в архиве повреждён") from exc
    if not isinstance(document, dict) or not isinstance(manifest, dict):
        raise ConfigurationBundleError("bundle.json и manifest.json должны содержать JSON-объекты")
    if document.get("product") != BUNDLE_PRODUCT or manifest.get("product") != BUNDLE_PRODUCT:
        raise ConfigurationBundleError("Архив создан другим продуктом")
    allowed_document_keys = {
        "product",
        "schema_version",
        "source_product_version",
        "exported_at",
        "safety_contract",
        "organization_reference",
        "entities",
    }
    allowed_manifest_keys = {
        "product",
        "schema_version",
        "source_product_version",
        "files",
        "contains_secrets",
        "credential_fields_included",
        "free_text_review_required",
        "entity_counts",
    }
    unexpected_document_keys = set(document) - allowed_document_keys
    unexpected_manifest_keys = set(manifest) - allowed_manifest_keys
    if unexpected_document_keys:
        raise ConfigurationBundleError(
            "bundle.json содержит неподдерживаемые поля: "
            + ", ".join(sorted(unexpected_document_keys))
        )
    if unexpected_manifest_keys:
        raise ConfigurationBundleError(
            "manifest.json содержит неподдерживаемые поля: "
            + ", ".join(sorted(unexpected_manifest_keys))
        )
    _assert_no_secret_fields(document)
    _assert_no_secret_fields(manifest, path="manifest")
    if (
        document.get("schema_version") != BUNDLE_SCHEMA_VERSION
        or manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION
    ):
        raise ConfigurationBundleError("Версия схемы конфигурационного архива не поддерживается")
    if manifest.get("contains_secrets") is not False:
        raise BundleSecurityError("Архив не подтверждает отсутствие credential-полей")
    if manifest.get("credential_fields_included") is not False:
        raise BundleSecurityError("Архив может содержать credential-поля")
    if manifest.get("free_text_review_required") is not True:
        raise BundleSecurityError("Архив не подтверждает необходимость проверки свободного текста")
    expected = manifest.get("files")
    if not isinstance(expected, dict):
        raise BundleSecurityError("Manifest содержит некорректный список файлов")
    for raw_path, digest in expected.items():
        _validate_zip_path(str(raw_path))
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BundleSecurityError("Manifest содержит некорректную контрольную сумму")
    actual_bundle_hash = hashlib.sha256(_canonical_json(document)).hexdigest()
    if expected.get("bundle.json") != actual_bundle_hash:
        raise BundleSecurityError("Контрольная сумма bundle.json не совпадает")
    unexpected = set(expected) - ({"bundle.json"} | set(files))
    if unexpected:
        raise BundleSecurityError("В архиве отсутствуют заявленные файлы")
    extra = set(files) - set(expected)
    if extra:
        raise BundleSecurityError("Архив содержит незаявленные файлы")
    for path, payload in files.items():
        if expected.get(path) != hashlib.sha256(payload).hexdigest():
            raise BundleSecurityError(f"Контрольная сумма файла {path} не совпадает")
    document["entities"] = _validate_document_shape(document)
    return document, files, manifest, signature_envelope


def _verify_bundle_signature(
    db: Session,
    *,
    organization_id: str,
    manifest: dict[str, Any],
    signature_envelope: dict[str, Any] | None,
    settings: Settings,
) -> SignatureVerification:
    """Реализовать внутренний этап verify bundle signature step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    verification = verify_signature(
        db,
        organization_id=organization_id,
        data=_canonical_json(manifest),
        envelope=signature_envelope,
        expected_purpose="teleflow.configuration_bundle.manifest.v1",
    )
    try:
        enforce_signature_policy(verification, policy=settings.artifact_signature_policy)
    except ArtifactSigningError as exc:
        raise BundleSecurityError(str(exc)) from exc
    return verification


def _unique_name(
    db: Session, model: Any, organization_id: str, base: str, *, mode: str
) -> tuple[str, Any | None, bool]:
    """Реализовать внутренний этап unique name step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    existing = db.scalar(
        select(model).where(model.organization_id == organization_id, model.name == base)
    )
    if not existing:
        return base, None, False
    if mode == "skip":
        return base, existing, False
    for index in range(2, 1000):
        candidate = f"{base} (импорт {index})"[:180]
        if not db.scalar(
            select(model).where(model.organization_id == organization_id, model.name == candidate)
        ):
            return candidate, None, True
    raise ConfigurationBundleError("Не удалось подобрать уникальное имя")


def preview_bundle(
    db: Session,
    *,
    organization_id: str,
    data: bytes,
    settings: Settings,
) -> dict[str, Any]:
    """Выполнить операцию preview bundle. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    document, files, manifest, signature_envelope = parse_bundle(data, settings=settings)
    verification = _verify_bundle_signature(
        db,
        organization_id=organization_id,
        manifest=manifest,
        signature_envelope=signature_envelope,
        settings=settings,
    )
    entities = document["entities"]
    conflicts: list[dict[str, Any]] = []
    name_models: dict[str, Any] = {
        "connections": TelegramConnection,
        "templates": MessageTemplate,
        "campaigns": Campaign,
        "automation_flows": AutomationFlow,
        "ai_providers": AIProviderConfig,
        "automation_policies": AutomationPolicy,
        "integrations": IntegrationEndpoint,
    }
    for entity_name, model in name_models.items():
        for item in entities.get(entity_name, []):
            name = str(item.get("name") or "")
            if name and db.scalar(
                select(model).where(model.organization_id == organization_id, model.name == name)
            ):
                conflicts.append({"entity": entity_name, "name": name, "reason": "name_exists"})
    for item in entities.get("destinations", []):
        chat_id = item.get("telegram_chat_id")
        if chat_id is None:
            continue
        source_connection = next(
            (
                row
                for row in entities.get("connections", [])
                if row.get("ref") == item.get("connection_ref")
            ),
            None,
        )
        if not source_connection:
            continue
        existing_connection = db.scalar(
            select(TelegramConnection).where(
                TelegramConnection.organization_id == organization_id,
                TelegramConnection.name == source_connection.get("name"),
            )
        )
        if existing_connection and db.scalar(
            select(Destination).where(
                Destination.connection_id == existing_connection.id,
                Destination.telegram_chat_id == chat_id,
                Destination.topic_id == item.get("topic_id"),
            )
        ):
            conflicts.append(
                {"entity": "destinations", "name": item.get("title"), "reason": "chat_exists"}
            )
    warnings = [
        "Credential-поля Telegram, AI и интеграций не входят в архив; свободный текст требует ручной проверки.",
        "Подключения будут созданы как черновики без credentials.",
        "Назначения будут отключены, не проверены и потребуют нового подтверждения разрешения.",
        "Кампании и автоматизации будут импортированы выключенными или как черновики.",
        "Проверьте пользовательские тексты, шаблоны и базу знаний: credential-поля исключены, но свободный текст переносится как конфигурация.",
    ]
    if verification.status == ArtifactSignatureStatus.UNSIGNED:
        warnings.append("Архив не подписан Ed25519; политика текущей среды разрешает такой импорт.")
    elif verification.status == ArtifactSignatureStatus.VALID_UNTRUSTED:
        warnings.append(
            "Подпись криптографически верна, но публичный ключ не добавлен в доверенные."
        )
    if not files and any(item.get("archive_path") for item in entities.get("media", [])):
        warnings.append("Медиафайлы заявлены, но отсутствуют в архиве.")
    summary = {key: len(value) for key, value in entities.items()}
    return {
        "valid": True,
        "schema_version": int(document["schema_version"]),
        "source_product_version": document.get("source_product_version"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "summary": summary,
        "conflicts": conflicts,
        "warnings": warnings,
        "manifest": manifest,
        "signature": verification.to_dict(),
    }


def apply_bundle(
    db: Session,
    *,
    organization_id: str,
    data: bytes,
    settings: Settings,
    storage: StorageService,
    user: User,
    conflict_mode: str,
) -> tuple[ConfigurationBundle, dict[str, int], dict[str, int], dict[str, int], list[str]]:
    """Выполнить операцию apply bundle. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if conflict_mode not in {"skip", "rename"}:
        raise ConfigurationBundleError("Допустимы conflict_mode=skip или rename")
    document, files, manifest, signature_envelope = parse_bundle(data, settings=settings)
    verification = _verify_bundle_signature(
        db,
        organization_id=organization_id,
        manifest=manifest,
        signature_envelope=signature_envelope,
        settings=settings,
    )
    entities = document["entities"]
    created: dict[str, int] = {key: 0 for key in entities}
    skipped: dict[str, int] = {key: 0 for key in entities}
    renamed: dict[str, int] = {key: 0 for key in entities}
    warnings: list[str] = []
    if verification.status == ArtifactSignatureStatus.UNSIGNED:
        warnings.append("Импортирован архив без Ed25519-подписи.")
    elif verification.status == ArtifactSignatureStatus.VALID_UNTRUSTED:
        warnings.append("Подпись архива верна, но ключ не отмечен доверенным в этой организации.")
    written_keys: list[str] = []
    maps: dict[str, dict[str, str]] = {
        "connections": {},
        "destinations": {},
        "media": {},
        "templates": {},
        "campaigns": {},
        "flows": {},
        "providers": {},
    }

    try:
        for item in entities.get("media", []):
            ref = str(item.get("ref"))
            existing = db.scalar(
                select(MediaAsset).where(
                    MediaAsset.organization_id == organization_id,
                    MediaAsset.sha256 == item.get("sha256"),
                )
            )
            if existing:
                maps["media"][ref] = existing.id
                skipped["media"] += 1
                continue
            archive_path = item.get("archive_path")
            if not archive_path or archive_path not in files:
                skipped["media"] += 1
                warnings.append(
                    f"Медиа {item.get('original_name') or ref} пропущено: файл не включён"
                )
                continue
            payload = files[archive_path]
            content_type = str(item.get("content_type") or "application/octet-stream").lower()
            try:
                digest = validate_media_bytes(payload, content_type, settings.max_media_bytes)
                scan_media_bytes(payload, settings)
            except (MediaValidationError, MalwareDetected) as exc:
                raise ConfigurationBundleError(f"Медиа {item.get('original_name')}: {exc}") from exc
            except AntivirusUnavailable as exc:
                if settings.antivirus_fail_closed:
                    raise ConfigurationBundleError(
                        "Антивирус недоступен; импорт остановлен"
                    ) from exc
                warnings.append("ClamAV недоступен: файл принят только из-за fail-open policy")
            if digest != item.get("sha256"):
                raise BundleSecurityError("SHA-256 импортируемого медиа не совпадает")
            suffix = Path(str(item.get("original_name") or "")).suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                suffix = ""
            stored_name = f"{uuid.uuid4().hex}{suffix}"
            key = f"media/{organization_id}/{stored_name}"
            storage.put_bytes(key, payload, content_type=content_type)
            written_keys.append(key)
            asset = MediaAsset(
                organization_id=organization_id,
                original_name=_safe_filename(str(item.get("original_name") or stored_name)),
                stored_name=stored_name,
                relative_path=key,
                storage_backend=settings.storage_backend,
                storage_key=key,
                content_type=content_type,
                size_bytes=len(payload),
                sha256=digest,
                created_by_id=user.id,
            )
            db.add(asset)
            db.flush()
            maps["media"][ref] = asset.id
            created["media"] += 1

        for item in entities.get("connections", []):
            ref = str(item.get("ref"))
            name, existing, was_renamed = _unique_name(
                db,
                TelegramConnection,
                organization_id,
                str(item.get("name") or "Telegram"),
                mode=conflict_mode,
            )
            if existing:
                maps["connections"][ref] = existing.id
                skipped["connections"] += 1
                continue
            kind = ConnectionKind(str(item.get("kind") or ConnectionKind.BOT.value))
            minimum = (
                settings.user_hard_min_interval_seconds
                if kind == ConnectionKind.USER
                else settings.bot_hard_min_interval_seconds
            )
            connection = TelegramConnection(
                organization_id=organization_id,
                name=name[:120],
                kind=kind,
                status=ConnectionStatus.DRAFT,
                credentials_enc=None,
                min_interval_seconds=max(minimum, int(item.get("min_interval_seconds") or minimum)),
                daily_cap=min(
                    settings.global_hard_daily_cap, max(1, int(item.get("daily_cap") or 1))
                ),
                destination_cooldown_minutes=max(
                    1,
                    int(
                        item.get("destination_cooldown_minutes")
                        or settings.default_destination_cooldown_minutes
                    ),
                ),
                require_manual_approval=True,
                stop_on_flood=True,
                created_by_id=user.id,
            )
            db.add(connection)
            db.flush()
            maps["connections"][ref] = connection.id
            created["connections"] += 1
            renamed["connections"] += int(was_renamed)

        for item in entities.get("destinations", []):
            ref = str(item.get("ref"))
            connection_id = maps["connections"].get(str(item.get("connection_ref")))
            if not connection_id:
                skipped["destinations"] += 1
                warnings.append(f"Назначение {item.get('title') or ref} пропущено: нет подключения")
                continue
            chat_id = item.get("telegram_chat_id")
            destination_existing = None
            if chat_id is not None:
                destination_existing = db.scalar(
                    select(Destination).where(
                        Destination.connection_id == connection_id,
                        Destination.telegram_chat_id == int(chat_id),
                        Destination.topic_id == item.get("topic_id"),
                    )
                )
            if destination_existing:
                maps["destinations"][ref] = destination_existing.id
                skipped["destinations"] += 1
                continue
            permission_reference = item.get("permission_reference") or {}
            note_parts = [
                "Импортировано как справка; требуется повторное подтверждение разрешения."
            ]
            if permission_reference.get("note"):
                note_parts.append(str(permission_reference["note"]))
            destination = Destination(
                organization_id=organization_id,
                connection_id=connection_id,
                telegram_chat_id=int(chat_id) if chat_id is not None else None,
                username=(str(item.get("username")).lstrip("@").strip() or None)
                if item.get("username")
                else None,
                title=str(item.get("title") or "Импортированная группа")[:255],
                kind=DestinationKind(str(item.get("kind") or DestinationKind.GROUP.value)),
                topic_id=item.get("topic_id"),
                enabled=False,
                validated=False,
                validated_at=None,
                validation_expires_at=None,
                permission_status=PermissionStatus.UNVERIFIED,
                permission_note="\n".join(note_parts)[:4000],
                rules_url=(
                    str(permission_reference.get("rules_url"))[:1000]
                    if permission_reference.get("rules_url")
                    else None
                ),
                permission_confirmed_at=None,
                permission_confirmed_by_id=None,
                permission_reviewed_at=None,
                permission_expires_at=None,
                timezone_name=item.get("timezone_name"),
                allowed_weekdays=list(item.get("allowed_weekdays") or []),
                allowed_start_time=_parse_time(item.get("allowed_start_time")),
                allowed_end_time=_parse_time(item.get("allowed_end_time")),
                cooldown_minutes_override=(
                    max(1, int(item.get("cooldown_minutes_override")))
                    if item.get("cooldown_minutes_override") is not None
                    else None
                ),
            )
            db.add(destination)
            db.flush()
            maps["destinations"][ref] = destination.id
            created["destinations"] += 1

        for item in entities.get("templates", []):
            ref = str(item.get("ref"))
            name, existing, was_renamed = _unique_name(
                db,
                MessageTemplate,
                organization_id,
                str(item.get("name") or "Шаблон"),
                mode=conflict_mode,
            )
            if existing:
                maps["templates"][ref] = existing.id
                skipped["templates"] += 1
                continue
            template = MessageTemplate(
                organization_id=organization_id,
                name=name[:150],
                body=str(item.get("body") or ""),
                parse_mode=ParseMode(str(item.get("parse_mode") or ParseMode.PLAIN.value)),
                media_asset_id=maps["media"].get(str(item.get("media_ref")))
                if item.get("media_ref")
                else None,
                link_preview=bool(item.get("link_preview", True)),
                is_active=False,
                revision=max(1, int(item.get("revision") or 1)),
                created_by_id=user.id,
            )
            db.add(template)
            db.flush()
            maps["templates"][ref] = template.id
            created["templates"] += 1
            renamed["templates"] += int(was_renamed)

        for item in entities.get("campaigns", []):
            ref = str(item.get("ref"))
            connection_id = maps["connections"].get(str(item.get("connection_ref")))
            template_id = maps["templates"].get(str(item.get("template_ref")))
            if not connection_id or not template_id:
                skipped["campaigns"] += 1
                warnings.append(
                    f"Кампания {item.get('name') or ref} пропущена: отсутствует подключение или шаблон"
                )
                continue
            name, existing, was_renamed = _unique_name(
                db,
                Campaign,
                organization_id,
                str(item.get("name") or "Импортированная кампания"),
                mode=conflict_mode,
            )
            if existing:
                maps["campaigns"][ref] = existing.id
                skipped["campaigns"] += 1
                continue
            schedule_at = _parse_datetime(item.get("schedule_at")) or utcnow()
            campaign = Campaign(
                organization_id=organization_id,
                name=name[:180],
                connection_id=connection_id,
                template_id=template_id,
                secondary_template_id=maps["templates"].get(str(item.get("secondary_template_ref")))
                if item.get("secondary_template_ref")
                else None,
                secondary_template_weight=max(
                    0, min(99, int(item.get("secondary_template_weight") or 0))
                ),
                status=CampaignStatus.DRAFT,
                schedule_type=ScheduleType(
                    str(item.get("schedule_type") or ScheduleType.ONCE.value)
                ),
                schedule_at=schedule_at,
                timezone_name=str(item.get("timezone_name") or "UTC")[:80],
                weekdays=list(item.get("weekdays") or []),
                spacing_seconds=max(1, int(item.get("spacing_seconds") or 90)),
                rollout_mode=RolloutMode(str(item.get("rollout_mode") or RolloutMode.STAGED.value)),
                rollout_batch_size=max(1, int(item.get("rollout_batch_size") or 5)),
                rollout_pause_seconds=max(0, int(item.get("rollout_pause_seconds") or 0)),
                rollout_require_checkpoint=bool(item.get("rollout_require_checkpoint", True)),
                rollout_failure_threshold_percent=max(
                    0, min(100, int(item.get("rollout_failure_threshold_percent") or 20))
                ),
                duplicate_guard_minutes=max(0, int(item.get("duplicate_guard_minutes") or 1380)),
                next_run_at=None,
                last_run_at=None,
                end_at=_parse_datetime(item.get("end_at")),
                manual_approval_required=True,
                approved_at=None,
                approved_by_id=None,
                approved_fingerprint=None,
                active_approval_request_id=None,
                created_by_id=user.id,
                notes=item.get("notes"),
            )
            db.add(campaign)
            db.flush()
            for link in item.get("destinations") or []:
                destination_id = maps["destinations"].get(str(link.get("destination_ref")))
                if not destination_id:
                    continue
                db.add(
                    CampaignDestination(
                        organization_id=organization_id,
                        campaign_id=campaign.id,
                        destination_id=destination_id,
                        position=max(0, int(link.get("position") or 0)),
                        custom_body=link.get("custom_body"),
                        enabled=bool(link.get("enabled", True)),
                    )
                )
            maps["campaigns"][ref] = campaign.id
            created["campaigns"] += 1
            renamed["campaigns"] += int(was_renamed)

        for item in entities.get("automation_flows", []):
            ref = str(item.get("ref"))
            name, existing, was_renamed = _unique_name(
                db,
                AutomationFlow,
                organization_id,
                str(item.get("name") or "Сценарий"),
                mode=conflict_mode,
            )
            if existing:
                maps["flows"][ref] = existing.id
                skipped["automation_flows"] += 1
                continue
            flow = AutomationFlow(
                organization_id=organization_id,
                name=name[:160],
                description=item.get("description"),
                is_active=False,
                revision=max(1, int(item.get("revision") or 1)),
                definition=dict(item.get("definition") or {}),
                created_by_id=user.id,
            )
            db.add(flow)
            db.flush()
            maps["flows"][ref] = flow.id
            created["automation_flows"] += 1
            renamed["automation_flows"] += int(was_renamed)

        for item in entities.get("ai_providers", []):
            ref = str(item.get("ref"))
            name, existing, was_renamed = _unique_name(
                db,
                AIProviderConfig,
                organization_id,
                str(item.get("name") or "AI provider"),
                mode=conflict_mode,
            )
            if existing:
                maps["providers"][ref] = existing.id
                skipped["ai_providers"] += 1
                continue
            provider = AIProviderConfig(
                organization_id=organization_id,
                name=name[:140],
                kind=AIProviderKind(str(item.get("kind") or AIProviderKind.RULE_BASED.value)),
                base_url=item.get("base_url"),
                model_name=item.get("model_name"),
                api_key_enc=None,
                enabled=False,
                timeout_seconds=max(1, int(item.get("timeout_seconds") or 30)),
                max_output_tokens=max(1, int(item.get("max_output_tokens") or 500)),
                temperature_milli=max(0, min(2000, int(item.get("temperature_milli") or 200))),
                data_region=item.get("data_region"),
                system_prompt=str(item.get("system_prompt") or ""),
                allowed_models=list(item.get("allowed_models") or []),
                created_by_id=user.id,
            )
            db.add(provider)
            db.flush()
            maps["providers"][ref] = provider.id
            created["ai_providers"] += 1
            renamed["ai_providers"] += int(was_renamed)

        for item in entities.get("automation_policies", []):
            connection_id = maps["connections"].get(str(item.get("connection_ref")))
            if not connection_id:
                skipped["automation_policies"] += 1
                continue
            name, existing, was_renamed = _unique_name(
                db,
                AutomationPolicy,
                organization_id,
                str(item.get("name") or "Политика"),
                mode=conflict_mode,
            )
            if existing:
                skipped["automation_policies"] += 1
                continue
            policy = AutomationPolicy(
                organization_id=organization_id,
                name=name[:160],
                telegram_connection_id=connection_id,
                ai_provider_config_id=maps["providers"].get(str(item.get("provider_ref")))
                if item.get("provider_ref")
                else None,
                automation_flow_id=maps["flows"].get(str(item.get("flow_ref")))
                if item.get("flow_ref")
                else None,
                enabled=False,
                timezone_name=str(item.get("timezone_name") or "UTC")[:80],
                active_hours=dict(item.get("active_hours") or {}),
                allowed_chat_types=list(item.get("allowed_chat_types") or ["private"]),
                consent_notice=str(item.get("consent_notice") or "")[:10000],
                fallback_message=str(item.get("fallback_message") or "")[:10000],
                max_auto_replies_per_day=max(1, int(item.get("max_auto_replies_per_day") or 20)),
                require_consent_before_ai=bool(item.get("require_consent_before_ai", True)),
                handoff_keywords=list(item.get("handoff_keywords") or []),
                stop_words=list(item.get("stop_words") or []),
                vacancy_detection_rules=dict(item.get("vacancy_detection_rules") or {}),
                created_by_id=user.id,
            )
            db.add(policy)
            created["automation_policies"] += 1
            renamed["automation_policies"] += int(was_renamed)

        for item in entities.get("knowledge_articles", []):
            article = KnowledgeBaseArticle(
                organization_id=organization_id,
                title=str(item.get("title") or "Статья")[:180],
                content=str(item.get("content") or ""),
                tags=list(item.get("tags") or []),
                vacancy_key=item.get("vacancy_key"),
                is_active=False,
                revision=max(1, int(item.get("revision") or 1)),
                created_by_id=user.id,
            )
            db.add(article)
            created["knowledge_articles"] += 1

        for item in entities.get("integrations", []):
            name, existing, was_renamed = _unique_name(
                db,
                IntegrationEndpoint,
                organization_id,
                str(item.get("name") or "Интеграция"),
                mode=conflict_mode,
            )
            if existing:
                skipped["integrations"] += 1
                continue
            endpoint = IntegrationEndpoint(
                organization_id=organization_id,
                name=name[:160],
                kind=IntegrationKind(str(item.get("kind") or IntegrationKind.WEBHOOK.value)),
                config_enc=None,
                event_types=list(item.get("event_types") or []),
                is_active=False,
                created_by_id=user.id,
            )
            db.add(endpoint)
            created["integrations"] += 1
            renamed["integrations"] += int(was_renamed)

        # Blackouts are imported last because their targets may have been skipped or mapped.
        for item in entities.get("blackouts", []):
            scope = BlackoutScope(str(item.get("scope") or BlackoutScope.ORGANIZATION.value))
            connection_id = (
                maps["connections"].get(str(item.get("connection_ref")))
                if item.get("connection_ref")
                else None
            )
            destination_id = (
                maps["destinations"].get(str(item.get("destination_ref")))
                if item.get("destination_ref")
                else None
            )
            if scope == BlackoutScope.CONNECTION and not connection_id:
                skipped["blackouts"] += 1
                continue
            if scope == BlackoutScope.DESTINATION and not destination_id:
                skipped["blackouts"] += 1
                continue
            blackout = PublishingBlackout(
                organization_id=organization_id,
                scope=scope,
                kind=BlackoutKind(str(item.get("kind") or BlackoutKind.ONE_TIME.value)),
                connection_id=connection_id,
                destination_id=destination_id,
                title=str(item.get("title") or "Импортированный запрет")[:180],
                reason=str(item.get("reason") or "Импортировано; требуется ручная проверка"),
                enabled=False,
                starts_at=_parse_datetime(item.get("starts_at")),
                ends_at=_parse_datetime(item.get("ends_at")),
                timezone_name=item.get("timezone_name"),
                weekdays=list(item.get("weekdays") or []),
                start_time=_parse_time(item.get("start_time")),
                end_time=_parse_time(item.get("end_time")),
                created_by_id=user.id,
            )
            db.add(blackout)
            created["blackouts"] += 1

        key = f"configuration-bundles/{organization_id}/{uuid.uuid4().hex}-import.zip"
        storage.put_bytes(key, data, content_type="application/zip")
        written_keys.append(key)
        bundle = ConfigurationBundle(
            organization_id=organization_id,
            kind=ConfigurationBundleKind.IMPORT,
            status=ConfigurationBundleStatus.IMPORTED,
            schema_version=BUNDLE_SCHEMA_VERSION,
            source_product_version=document.get("source_product_version"),
            filename=f"import-{utcnow().strftime('%Y%m%d-%H%M%S')}.zip",
            storage_key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            include_media=bool(files),
            conflict_mode=conflict_mode,
            manifest=manifest,
            summary={"created": created, "skipped": skipped, "renamed": renamed},
            signature_status=verification.status,
            signature_info=signature_envelope or {},
            signer_fingerprint=verification.fingerprint,
            created_by_id=user.id,
            applied_by_id=user.id,
            applied_at=utcnow(),
            created_at=utcnow(),
        )
        db.add(bundle)
        db.flush()
        warnings.extend(
            [
                "Подключения импортированы без credential-полей и требуют повторной авторизации.",
                "Группы отключены и требуют live-проверки и нового подтверждения разрешения.",
                "Шаблоны, AI, сценарии, политики, интеграции и запреты импортированы выключенными.",
                "Кампании импортированы как черновики без утверждений и расписания запуска.",
            ]
        )
        return bundle, created, skipped, renamed, warnings
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        for key in reversed(written_keys):
            try:
                storage.delete(key)
            except Exception:
                pass
        raise ConfigurationBundleError(
            "Конфигурационный архив содержит недопустимые значения"
        ) from exc
    except Exception:
        for key in reversed(written_keys):
            try:
                storage.delete(key)
            except Exception:
                pass
        raise
