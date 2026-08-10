from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_cipher, get_current_user, require_roles
from app.enums import SafetySeverity, UserRole
from app.models import ReleaseAttestation, User
from app.schemas import ReleaseAttestationImport, ReleaseAttestationRead
from app.services.crypto import SecretCipher
from app.services.release_trust import (
    ReleaseTrustError,
    create_local_release_attestation,
    import_release_attestation,
    verify_release_attestation,
)

router = APIRouter(prefix="/release-attestations", tags=["release-trust"])


def _item_or_404(
    db: Session, item_id: str, organization_id: str, *, for_update: bool = False
) -> ReleaseAttestation:
    """Реализовать внутренний этап item or 404 step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(ReleaseAttestation).where(
        ReleaseAttestation.id == item_id,
        ReleaseAttestation.organization_id == organization_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if item is None:
        raise HTTPException(status_code=404, detail="Release attestation не найден")
    return item


@router.get("", response_model=list[ReleaseAttestationRead])
def list_release_attestations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать release attestations. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(ReleaseAttestation)
            .where(ReleaseAttestation.organization_id == user.organization_id)
            .order_by(ReleaseAttestation.created_at.desc())
            .limit(200)
        ).all()
    )


@router.post("/local", response_model=ReleaseAttestationRead, status_code=201)
def create_local(
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
):
    """Создать local. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    try:
        item = create_local_release_attestation(
            db,
            organization_id=user.organization_id,
            user=user,
            settings=settings,
            cipher=cipher,
        )
    except ReleaseTrustError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        action="release_attestation.created_local",
        actor=user,
        entity_type="release_attestation",
        entity_id=item.id,
        severity=SafetySeverity.WARNING,
        details={
            "version": item.version,
            "payload_sha256": item.payload_sha256,
            "signature_status": item.signature_status.value,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/import", response_model=ReleaseAttestationRead, status_code=201)
def import_item(
    payload: ReleaseAttestationImport,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Создать item. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    try:
        item = import_release_attestation(
            db,
            organization_id=user.organization_id,
            user=user,
            payload=payload.payload,
            signature=payload.signature,
        )
    except ReleaseTrustError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        action="release_attestation.imported",
        actor=user,
        entity_type="release_attestation",
        entity_id=item.id,
        severity=SafetySeverity.WARNING,
        details={
            "version": item.version,
            "payload_sha256": item.payload_sha256,
            "signature_status": item.signature_status.value,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/{item_id}/verify", response_model=ReleaseAttestationRead)
def verify_item(
    item_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Проверить item. Некорректные данные или состояние отклоняются до побочного эффекта."""
    item = _item_or_404(db, item_id, user.organization_id, for_update=True)
    trusted = verify_release_attestation(db, item)
    write_audit(
        db,
        action="release_attestation.verified",
        actor=user,
        entity_type="release_attestation",
        entity_id=item.id,
        severity=SafetySeverity.INFO,
        details={
            "trusted": trusted,
            "signature_status": item.signature_status.value,
            "payload_sha256": item.payload_sha256,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item
