from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import (
    get_app_settings,
    get_cipher,
    get_current_user,
    require_roles,
)
from app.enums import ArtifactSigningKeyStatus, SafetySeverity, UserRole
from app.models import ArtifactSigningKey, User
from app.schemas import (
    ArtifactInspectionRead,
    ArtifactPublicKeyExport,
    ArtifactSigningKeyGenerateRequest,
    ArtifactSigningKeyImportRequest,
    ArtifactSigningKeyPatch,
    ArtifactSigningKeyRead,
    ArtifactSigningKeyRevokeRequest,
    MessageResponse,
)
from app.services.artifact_signing import (
    ArtifactSigningError,
    generate_signing_key,
    import_trusted_public_key,
    public_key_pem,
    revoke_signing_key,
    set_default_signing_key,
)
from app.services.artifact_verifier import ArtifactVerificationError, inspect_artifact
from app.services.crypto import SecretCipher

router = APIRouter(prefix="/artifact-signing-keys", tags=["artifact-signing"])


def _key_or_404(
    db: Session,
    *,
    key_id: str,
    organization_id: str,
    for_update: bool = False,
) -> ArtifactSigningKey:
    """Реализовать внутренний этап key or 404 step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(ArtifactSigningKey).where(
        ArtifactSigningKey.id == key_id,
        ArtifactSigningKey.organization_id == organization_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if item is None:
        raise HTTPException(status_code=404, detail="Ключ подписи не найден")
    return item


def _http_error(exc: ArtifactSigningError) -> HTTPException:
    """Реализовать внутренний этап http error step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return HTTPException(status_code=409, detail=str(exc))


@router.get("", response_model=list[ArtifactSigningKeyRead])
def list_artifact_signing_keys(
    include_revoked: bool = True,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ArtifactSigningKey]:
    """Прочитать artifact signing keys. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(ArtifactSigningKey).where(
        ArtifactSigningKey.organization_id == user.organization_id
    )
    if not include_revoked:
        stmt = stmt.where(ArtifactSigningKey.status == ArtifactSigningKeyStatus.ACTIVE)
    return list(
        db.scalars(
            stmt.order_by(
                ArtifactSigningKey.is_default.desc(),
                ArtifactSigningKey.created_at.desc(),
            )
        ).all()
    )


@router.post("/generate", response_model=ArtifactSigningKeyRead, status_code=201)
def generate_artifact_signing_key(
    payload: ArtifactSigningKeyGenerateRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    cipher: SecretCipher = Depends(get_cipher),
) -> ArtifactSigningKey:
    """Выполнить операцию generate artifact signing key. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    try:
        item = generate_signing_key(
            db,
            organization_id=user.organization_id,
            created_by=user,
            cipher=cipher,
            name=payload.name,
            make_default=payload.make_default,
            trusted_for_import=payload.trusted_for_import,
            note=payload.note,
        )
        write_audit(
            db,
            action="artifact_signing_key.generated",
            actor=user,
            entity_type="artifact_signing_key",
            entity_id=item.id,
            severity=SafetySeverity.WARNING,
            details={
                "key_id": item.key_id,
                "fingerprint": item.fingerprint,
                "algorithm": item.algorithm,
                "is_default": item.is_default,
                "trusted_for_import": item.trusted_for_import,
                "has_private_key": True,
            },
            request=request,
        )
        db.commit()
        db.refresh(item)
        return item
    except ArtifactSigningError as exc:
        db.rollback()
        raise _http_error(exc) from exc


@router.post("/import", response_model=ArtifactSigningKeyRead, status_code=201)
def import_artifact_public_key(
    payload: ArtifactSigningKeyImportRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> ArtifactSigningKey:
    """Создать artifact public key. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    try:
        item = import_trusted_public_key(
            db,
            organization_id=user.organization_id,
            created_by=user,
            name=payload.name,
            public_key=payload.public_key,
            trusted_for_import=payload.trusted_for_import,
            note=payload.note,
        )
        write_audit(
            db,
            action="artifact_signing_key.public_imported",
            actor=user,
            entity_type="artifact_signing_key",
            entity_id=item.id,
            severity=SafetySeverity.WARNING,
            details={
                "key_id": item.key_id,
                "fingerprint": item.fingerprint,
                "algorithm": item.algorithm,
                "trusted_for_import": item.trusted_for_import,
                "has_private_key": False,
            },
            request=request,
        )
        db.commit()
        db.refresh(item)
        return item
    except ArtifactSigningError as exc:
        db.rollback()
        raise _http_error(exc) from exc


@router.post("/verify-artifact", response_model=ArtifactInspectionRead)
async def verify_artifact_upload(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    """Проверить artifact upload. Некорректные данные или состояние отклоняются до побочного
    эффекта.
    """
    data = await file.read(settings.max_export_bytes + 1)
    if len(data) > settings.max_export_bytes:
        raise HTTPException(status_code=413, detail="Артефакт превышает допустимый размер")
    local_keys = list(
        db.scalars(
            select(ArtifactSigningKey).where(
                ArtifactSigningKey.organization_id == user.organization_id
            )
        ).all()
    )
    trusted = {
        key.fingerprint
        for key in local_keys
        if key.status == ArtifactSigningKeyStatus.ACTIVE and key.trusted_for_import
    }
    local_by_fingerprint = {key.fingerprint: key for key in local_keys}
    try:
        result = inspect_artifact(
            data,
            trusted_fingerprints=trusted,
            max_bytes=settings.max_export_bytes,
        )
        signature = result.get("signature") or {}
        fingerprint = str(signature.get("fingerprint") or "")
        local_key = local_by_fingerprint.get(fingerprint)
        if local_key is not None:
            signature["signer_known"] = True
            if local_key.status == ArtifactSigningKeyStatus.REVOKED:
                signature.update(
                    {
                        "status": "revoked",
                        "trusted": False,
                        "signer_revoked": True,
                    }
                )
        result["signature"] = signature
        return result
    except ArtifactVerificationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{key_id}", response_model=ArtifactSigningKeyRead)
def update_artifact_signing_key(
    key_id: str,
    payload: ArtifactSigningKeyPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> ArtifactSigningKey:
    """Обновить artifact signing key. Переход применяется только после проверки его предусловий."""
    item = _key_or_404(
        db,
        key_id=key_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    if item.status == ArtifactSigningKeyStatus.REVOKED:
        raise HTTPException(status_code=409, detail="Отозванный ключ нельзя изменять")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        normalized_name = str(changes["name"]).strip()
        if len(normalized_name) < 3:
            raise HTTPException(
                status_code=422,
                detail="Название ключа должно содержать не менее 3 символов",
            )
        item.name = normalized_name[:160]
    if "trusted_for_import" in changes:
        item.trusted_for_import = bool(changes["trusted_for_import"])
    if "note" in changes:
        item.note = str(changes["note"] or "").strip()[:2000] or None
    write_audit(
        db,
        action="artifact_signing_key.updated",
        actor=user,
        entity_type="artifact_signing_key",
        entity_id=item.id,
        severity=SafetySeverity.WARNING,
        details={
            "fingerprint": item.fingerprint,
            "changed_fields": sorted(changes),
            "trusted_for_import": item.trusted_for_import,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/{key_id}/default", response_model=ArtifactSigningKeyRead)
def make_artifact_signing_key_default(
    key_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> ArtifactSigningKey:
    """Выполнить операцию make artifact signing key default. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    item = _key_or_404(
        db,
        key_id=key_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    try:
        set_default_signing_key(db, key=item)
    except ArtifactSigningError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    write_audit(
        db,
        action="artifact_signing_key.default_changed",
        actor=user,
        entity_type="artifact_signing_key",
        entity_id=item.id,
        severity=SafetySeverity.WARNING,
        details={"fingerprint": item.fingerprint, "key_id": item.key_id},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/{key_id}/revoke", response_model=MessageResponse)
def revoke_artifact_signing_key(
    key_id: str,
    payload: ArtifactSigningKeyRevokeRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить revoke artifact signing key. Зависимое состояние и видимые в аудите
    последствия обрабатываются согласованно.
    """
    item = _key_or_404(
        db,
        key_id=key_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    revoke_signing_key(db, key=item, revoked_by=user, note=payload.reason)
    write_audit(
        db,
        action="artifact_signing_key.revoked",
        actor=user,
        entity_type="artifact_signing_key",
        entity_id=item.id,
        severity=SafetySeverity.CRITICAL,
        details={
            "fingerprint": item.fingerprint,
            "key_id": item.key_id,
            "reason": payload.reason,
        },
        request=request,
    )
    db.commit()
    return MessageResponse(message="Ключ подписи отозван")


@router.get("/{key_id}/public", response_model=ArtifactPublicKeyExport)
def export_artifact_public_key(
    key_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactPublicKeyExport:
    """Выполнить операцию export artifact public key. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    item = _key_or_404(
        db,
        key_id=key_id,
        organization_id=user.organization_id,
    )
    return ArtifactPublicKeyExport(
        key_id=item.key_id,
        fingerprint=item.fingerprint,
        algorithm=item.algorithm,
        public_key_b64=item.public_key_b64,
        public_key_pem=public_key_pem(item.public_key_b64),
    )
