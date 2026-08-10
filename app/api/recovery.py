from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_user, require_roles
from app.enums import RecoveryDrillStatus, SafetySeverity, UserRole
from app.models import RecoveryBackupEvidence, RecoveryPolicy, RecoveryRestoreDrill, User
from app.schemas import (
    RecoveryBackupEvidenceRead,
    RecoveryComplianceRead,
    RecoveryPolicyPatch,
    RecoveryPolicyRead,
    RecoveryRestoreDrillRead,
)
from app.services.recovery import (
    MAX_RECEIPT_BYTES,
    RecoveryError,
    evaluate_recovery_compliance,
    get_or_create_recovery_policy,
    import_backup_receipt,
    import_drill_receipt,
)

router = APIRouter(prefix="/recovery", tags=["recovery"])


def _read_receipt(file: UploadFile) -> bytes:
    """Реализовать внутренний этап read receipt step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    data = file.file.read(MAX_RECEIPT_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Receipt пуст")
    if len(data) > MAX_RECEIPT_BYTES:
        raise HTTPException(status_code=413, detail="Receipt превышает допустимый размер")
    return data


@router.get("/status", response_model=RecoveryComplianceRead)
def recovery_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    """Выполнить операцию recovery status. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    policy, compliance = evaluate_recovery_compliance(
        db,
        organization_id=user.organization_id,
        actor=user,
        settings=settings,
    )
    db.commit()
    return {
        "status": compliance.status,
        "policy": policy,
        "checks": compliance.checks,
        "blockers": compliance.blockers,
        "warnings": compliance.warnings,
        "latest_backup": compliance.latest_backup,
        "latest_drill": compliance.latest_drill,
    }


@router.get("/policy", response_model=RecoveryPolicyRead)
def get_recovery_policy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RecoveryPolicy:
    """Прочитать recovery policy. Значение возвращается без несвязанных изменений состояния."""
    policy = get_or_create_recovery_policy(
        db,
        organization_id=user.organization_id,
        actor=user,
        settings=settings,
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/policy", response_model=RecoveryPolicyRead)
def update_recovery_policy(
    payload: RecoveryPolicyPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RecoveryPolicy:
    """Обновить recovery policy. Переход применяется только после проверки его предусловий."""
    policy = get_or_create_recovery_policy(
        db,
        organization_id=user.organization_id,
        actor=user,
        settings=settings,
    )
    changes = payload.model_dump(exclude_unset=True)
    if settings.is_production:
        forbidden_false = {
            "enabled": "В production контроль восстановления нельзя отключить",
            "require_encrypted_backup": "В production backup должен оставаться зашифрованным",
            "require_trusted_signature": "В production требуется доверенная подпись receipt",
            "require_restore_drill": "В production restore drill обязателен",
        }
        for field, message in forbidden_false.items():
            if changes.get(field) is False:
                raise HTTPException(status_code=400, detail=message)
    for field, value in changes.items():
        setattr(policy, field, value)
    policy.updated_by_id = user.id
    write_audit(
        db,
        action="recovery.policy.updated",
        actor=user,
        entity_type="recovery_policy",
        entity_id=policy.id,
        severity=SafetySeverity.WARNING,
        details={"changed_fields": sorted(changes)},
        request=request,
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.get("/backups", response_model=list[RecoveryBackupEvidenceRead])
def list_recovery_backups(
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RecoveryBackupEvidence]:
    """Прочитать recovery backups. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(RecoveryBackupEvidence)
            .where(RecoveryBackupEvidence.organization_id == user.organization_id)
            .order_by(RecoveryBackupEvidence.backup_created_at.desc())
            .limit(min(max(limit, 1), 500))
        ).all()
    )


@router.post(
    "/backups/import",
    response_model=RecoveryBackupEvidenceRead,
    status_code=status.HTTP_201_CREATED,
)
def import_recovery_backup(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RecoveryBackupEvidence:
    """Создать recovery backup. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    data = _read_receipt(file)
    try:
        evidence = import_backup_receipt(
            db,
            data=data,
            organization_id=user.organization_id,
            actor=user,
            settings=settings,
        )
    except RecoveryError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        db,
        action="recovery.backup_receipt.imported",
        actor=user,
        entity_type="recovery_backup_evidence",
        entity_id=evidence.id,
        severity=SafetySeverity.INFO,
        details={
            "backup_id": evidence.backup_id,
            "artifact_sha256": evidence.artifact_sha256,
            "encrypted": evidence.artifact_encrypted,
            "signature_status": evidence.signature_status.value,
            "backup_created_at": evidence.backup_created_at.isoformat(),
        },
        request=request,
    )
    db.commit()
    db.refresh(evidence)
    return evidence


@router.get("/drills", response_model=list[RecoveryRestoreDrillRead])
def list_recovery_drills(
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RecoveryRestoreDrill]:
    """Прочитать recovery drills. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(RecoveryRestoreDrill)
            .where(RecoveryRestoreDrill.organization_id == user.organization_id)
            .order_by(RecoveryRestoreDrill.completed_at.desc())
            .limit(min(max(limit, 1), 500))
        ).all()
    )


@router.post(
    "/drills/import",
    response_model=RecoveryRestoreDrillRead,
    status_code=status.HTTP_201_CREATED,
)
def import_recovery_drill(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RecoveryRestoreDrill:
    """Создать recovery drill. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    data = _read_receipt(file)
    try:
        drill = import_drill_receipt(
            db,
            data=data,
            organization_id=user.organization_id,
            actor=user,
            settings=settings,
        )
    except RecoveryError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        db,
        action="recovery.restore_drill.imported",
        actor=user,
        entity_type="recovery_restore_drill",
        entity_id=drill.id,
        severity=(
            SafetySeverity.INFO
            if drill.status == RecoveryDrillStatus.PASSED
            else SafetySeverity.CRITICAL
        ),
        details={
            "drill_id": drill.drill_id,
            "backup_id": drill.backup_id,
            "status": drill.status.value,
            "duration_seconds": drill.duration_seconds,
            "rto_met": drill.rto_met,
            "signature_status": drill.signature_status.value,
        },
        request=request,
    )
    db.commit()
    db.refresh(drill)
    return drill
