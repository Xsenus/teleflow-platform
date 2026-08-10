from __future__ import annotations

import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import (
    get_app_settings,
    get_current_organization,
    get_current_user,
    require_roles,
)
from app.enums import ConfigurationBundleKind, SafetySeverity, UserRole
from app.models import ConfigurationBundle, Organization, User
from app.schemas import (
    ConfigurationBundleExportRequest,
    ConfigurationBundleImportResult,
    ConfigurationBundlePreview,
    ConfigurationBundleRead,
    MessageResponse,
)
from app.services.config_bundles import (
    BundleSecurityError,
    ConfigurationBundleError,
    apply_bundle,
    create_export_bundle,
    preview_bundle,
)

router = APIRouter(prefix="/configuration-bundles", tags=["configuration-bundles"])


def _bundle_or_404(db: Session, *, bundle_id: str, organization_id: str) -> ConfigurationBundle:
    """Реализовать внутренний этап bundle or 404 step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    bundle = db.scalar(
        select(ConfigurationBundle).where(
            ConfigurationBundle.id == bundle_id,
            ConfigurationBundle.organization_id == organization_id,
        )
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail="Конфигурационный архив не найден")
    return bundle


async def _read_zip(file: UploadFile, *, limit: int) -> bytes:
    """Реализовать внутренний этап read zip step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    content_type = (file.content_type or "application/octet-stream").lower()
    if content_type not in {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
    }:
        raise HTTPException(status_code=415, detail="Ожидается ZIP-архив TeleFlow")
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail="Архив превышает допустимый размер")
    if not data:
        raise HTTPException(status_code=400, detail="Загружен пустой файл")
    return data


def _bundle_http_error(exc: ConfigurationBundleError) -> HTTPException:
    """Реализовать внутренний этап bundle http error step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    status = 422 if isinstance(exc, BundleSecurityError) else 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("", response_model=list[ConfigurationBundleRead])
def list_configuration_bundles(
    kind: ConfigurationBundleKind | None = None,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConfigurationBundle]:
    """Прочитать configuration bundles. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(ConfigurationBundle).where(
        ConfigurationBundle.organization_id == user.organization_id
    )
    if kind is not None:
        stmt = stmt.where(ConfigurationBundle.kind == kind)
    return list(
        db.scalars(
            stmt.order_by(ConfigurationBundle.created_at.desc()).limit(min(max(limit, 1), 500))
        ).all()
    )


@router.post("/export", response_model=ConfigurationBundleRead, status_code=201)
def export_configuration_bundle(
    payload: ConfigurationBundleExportRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ConfigurationBundle:
    """Выполнить операцию export configuration bundle. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    try:
        bundle = create_export_bundle(
            db,
            organization=organization,
            created_by=user,
            settings=settings,
            storage=request.app.state.storage,
            cipher=request.app.state.cipher,
            include_media=payload.include_media,
        )
        write_audit(
            db,
            action="configuration_bundle.exported",
            actor=user,
            entity_type="configuration_bundle",
            entity_id=bundle.id,
            details={
                "sha256": bundle.sha256,
                "size_bytes": bundle.size_bytes,
                "include_media": bundle.include_media,
                "summary": bundle.summary,
                "contains_secrets": False,
                "signature_status": bundle.signature_status.value,
                "signer_fingerprint": bundle.signer_fingerprint,
            },
            request=request,
        )
        db.commit()
        db.refresh(bundle)
        return bundle
    except ConfigurationBundleError as exc:
        db.rollback()
        raise _bundle_http_error(exc) from exc


@router.post("/preview", response_model=ConfigurationBundlePreview)
async def preview_configuration_bundle(
    file: UploadFile = File(...),
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    """Выполнить операцию preview configuration bundle. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    data = await _read_zip(file, limit=settings.max_export_bytes)
    try:
        return preview_bundle(
            db,
            organization_id=user.organization_id,
            data=data,
            settings=settings,
        )
    except ConfigurationBundleError as exc:
        raise _bundle_http_error(exc) from exc


@router.post("/import", response_model=ConfigurationBundleImportResult, status_code=201)
async def import_configuration_bundle(
    request: Request,
    conflict_mode: Literal["skip", "rename"] = "rename",
    file: UploadFile = File(...),
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ConfigurationBundleImportResult:
    """Создать configuration bundle. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    data = await _read_zip(file, limit=settings.max_export_bytes)
    try:
        bundle, created, skipped, renamed, warnings = apply_bundle(
            db,
            organization_id=user.organization_id,
            data=data,
            settings=settings,
            storage=request.app.state.storage,
            user=user,
            conflict_mode=conflict_mode,
        )
        write_audit(
            db,
            action="configuration_bundle.imported",
            actor=user,
            entity_type="configuration_bundle",
            entity_id=bundle.id,
            severity=SafetySeverity.WARNING,
            details={
                "sha256": bundle.sha256,
                "size_bytes": bundle.size_bytes,
                "conflict_mode": conflict_mode,
                "created": created,
                "skipped": skipped,
                "renamed": renamed,
                "safety": {
                    "connections_have_no_credentials": True,
                    "destinations_disabled_and_unverified": True,
                    "campaigns_are_drafts": True,
                    "automations_disabled": True,
                },
                "signature_status": bundle.signature_status.value,
                "signer_fingerprint": bundle.signer_fingerprint,
            },
            request=request,
        )
        db.commit()
        db.refresh(bundle)
        return ConfigurationBundleImportResult(
            bundle=ConfigurationBundleRead.model_validate(bundle),
            created=created,
            skipped=skipped,
            renamed=renamed,
            warnings=warnings,
        )
    except ConfigurationBundleError as exc:
        db.rollback()
        write_audit(
            db,
            action="configuration_bundle.import_blocked",
            actor=user,
            entity_type="configuration_bundle",
            severity=SafetySeverity.WARNING,
            details={
                "error_type": type(exc).__name__,
                "error": str(exc),
                "size_bytes": len(data),
            },
            request=request,
        )
        db.commit()
        raise _bundle_http_error(exc) from exc


@router.get("/{bundle_id}/download")
def download_configuration_bundle(
    bundle_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Response:
    """Выполнить операцию download configuration bundle. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    bundle = _bundle_or_404(db, bundle_id=bundle_id, organization_id=user.organization_id)
    try:
        data = request.app.state.storage.read_bytes(bundle.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="Файл архива больше не доступен") from exc
    if bundle.sha256 != hashlib.sha256(data).hexdigest():
        raise HTTPException(status_code=409, detail="Контрольная сумма архива не совпадает")
    write_audit(
        db,
        action="configuration_bundle.downloaded",
        actor=user,
        entity_type="configuration_bundle",
        entity_id=bundle.id,
        details={
            "kind": bundle.kind.value,
            "sha256": bundle.sha256,
            "size_bytes": bundle.size_bytes,
        },
        request=request,
    )
    db.commit()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{bundle.filename}"',
            "X-Content-SHA256": bundle.sha256,
            "X-Artifact-Signature-Status": bundle.signature_status.value,
            "X-Artifact-Signer-Fingerprint": bundle.signer_fingerprint or "",
            "Cache-Control": "no-store",
        },
    )


@router.delete("/{bundle_id}", response_model=MessageResponse)
def delete_configuration_bundle(
    bundle_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete configuration bundle. Зависимое состояние и видимые в аудите
    последствия обрабатываются согласованно.
    """
    bundle = _bundle_or_404(db, bundle_id=bundle_id, organization_id=user.organization_id)
    request.app.state.storage.delete(bundle.storage_key)
    digest = bundle.sha256
    kind = bundle.kind.value
    db.delete(bundle)
    write_audit(
        db,
        action="configuration_bundle.deleted",
        actor=user,
        entity_type="configuration_bundle",
        entity_id=bundle_id,
        severity=SafetySeverity.WARNING,
        details={"kind": kind, "sha256": digest},
        request=request,
    )
    db.commit()
    return MessageResponse(message="Конфигурационный архив удалён")
