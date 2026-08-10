from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
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
from app.enums import (
    ChangeRequestStatus,
    ChangeRequestType,
    DeploymentVerificationPhase,
    ReadinessStatus,
    UserRole,
)
from app.models import (
    ChangeRequest,
    DeploymentVerificationReport,
    Organization,
    ReleaseAttestation,
    ReleaseDependencyAssessment,
    User,
    utcnow,
)
from app.schemas import (
    ChangeActionRequest,
    ChangeRequestCreate,
    ChangeRequestRead,
    DeploymentVerificationRead,
    MaintenanceStartRequest,
    MaintenanceStopRequest,
    OrganizationRead,
)
from app.services.change_management import change_fingerprint, run_deployment_verification

router = APIRouter(prefix="/changes", tags=["changes"])


@router.get("", response_model=list[ChangeRequestRead])
def list_changes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Прочитать changes. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(ChangeRequest)
            .where(ChangeRequest.organization_id == user.organization_id)
            .order_by(ChangeRequest.created_at.desc())
            .limit(200)
        ).all()
    )


@router.get("/{change_id}", response_model=ChangeRequestRead)
def get_change(
    change_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Прочитать change. Значение возвращается без несвязанных изменений состояния."""
    item = db.scalar(
        select(ChangeRequest).where(
            ChangeRequest.id == change_id, ChangeRequest.organization_id == user.organization_id
        )
    )
    if not item:
        raise HTTPException(404, "Изменение не найдено")
    return item


@router.post("", response_model=ChangeRequestRead, status_code=201)
def create_change(
    payload: ChangeRequestCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Создать change. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    attestation = None
    if payload.release_attestation_id:
        attestation = db.scalar(
            select(ReleaseAttestation).where(
                ReleaseAttestation.id == payload.release_attestation_id,
                ReleaseAttestation.organization_id == user.organization_id,
            )
        )
        if attestation is None:
            raise HTTPException(404, "Release attestation не найден в текущей организации")
        if (
            payload.change_type == ChangeRequestType.UPGRADE
            and payload.target_version
            and attestation.version != payload.target_version
        ):
            raise HTTPException(
                409,
                f"Release attestation относится к версии {attestation.version}, а выбрана {payload.target_version}",
            )
    assessment = None
    if payload.release_dependency_assessment_id:
        if not payload.release_attestation_id:
            raise HTTPException(
                409, "Dependency assessment должен быть привязан вместе с release attestation"
            )
        assessment = db.scalar(
            select(ReleaseDependencyAssessment).where(
                ReleaseDependencyAssessment.id == payload.release_dependency_assessment_id,
                ReleaseDependencyAssessment.organization_id == user.organization_id,
            )
        )
        if assessment is None:
            raise HTTPException(404, "Dependency assessment не найден в текущей организации")
        if assessment.release_attestation_id != payload.release_attestation_id:
            raise HTTPException(409, "Dependency assessment относится к другой release attestation")
    item = ChangeRequest(
        organization_id=user.organization_id,
        title=payload.title.strip(),
        change_type=payload.change_type,
        current_version=payload.current_version or settings.version,
        target_version=payload.target_version,
        reason=payload.reason.strip(),
        risk_summary=payload.risk_summary.strip(),
        rollback_plan=payload.rollback_plan.strip(),
        planned_start_at=payload.planned_start_at,
        planned_end_at=payload.planned_end_at,
        release_attestation_id=payload.release_attestation_id or None,
        release_dependency_assessment_id=payload.release_dependency_assessment_id or None,
        fingerprint="pending",
        created_by_id=user.id,
    )
    item.fingerprint = change_fingerprint(item)
    db.add(item)
    db.flush()
    write_audit(
        db,
        actor=user,
        action="change.created",
        entity_type="change_request",
        entity_id=item.id,
        details={"change_type": item.change_type.value, "fingerprint": item.fingerprint},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/{change_id}/approve", response_model=ChangeRequestRead)
def approve_change(
    change_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Выполнить операцию approve change. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    item = db.scalar(
        select(ChangeRequest)
        .where(ChangeRequest.id == change_id, ChangeRequest.organization_id == user.organization_id)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Изменение не найдено")
    if item.status != ChangeRequestStatus.DRAFT:
        raise HTTPException(409, "Изменение уже обработано")
    if item.created_by_id == user.id:
        raise HTTPException(409, "Автор изменения не может сам его утвердить")
    if item.fingerprint != change_fingerprint(item):
        raise HTTPException(409, "Содержимое change request изменилось")
    item.status = ChangeRequestStatus.APPROVED
    item.approved_by_id = user.id
    item.approved_at = utcnow()
    write_audit(
        db,
        actor=user,
        action="change.approved",
        entity_type="change_request",
        entity_id=item.id,
        details={"fingerprint": item.fingerprint},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/maintenance/start", response_model=OrganizationRead)
def start_maintenance(
    payload: MaintenanceStartRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    """Выполнить операцию start maintenance. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    organization.maintenance_mode = True
    organization.maintenance_reason = payload.reason.strip()
    organization.maintenance_started_at = utcnow()
    organization.maintenance_started_by_id = user.id
    write_audit(
        db,
        actor=user,
        action="maintenance.started",
        entity_type="organization",
        entity_id=organization.id,
        details={"reason": payload.reason},
        request=request,
    )
    db.commit()
    db.refresh(organization)
    return organization


@router.post("/maintenance/stop", response_model=OrganizationRead)
def stop_maintenance(
    payload: MaintenanceStopRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    """Выполнить операцию stop maintenance. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    active = db.scalar(
        select(ChangeRequest)
        .where(
            ChangeRequest.organization_id == organization.id,
            ChangeRequest.status == ChangeRequestStatus.IN_PROGRESS,
        )
        .limit(1)
    )
    if active:
        raise HTTPException(409, "Сначала завершите активное изменение")
    organization.maintenance_mode = False
    organization.maintenance_reason = None
    organization.maintenance_started_at = None
    organization.maintenance_started_by_id = None
    write_audit(
        db,
        actor=user,
        action="maintenance.stopped",
        entity_type="organization",
        entity_id=organization.id,
        details={"note": payload.note},
        request=request,
    )
    db.commit()
    db.refresh(organization)
    return organization


@router.get("/{change_id}/verifications", response_model=list[DeploymentVerificationRead])
def list_change_verifications(
    change_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Прочитать change verifications. Значение возвращается без несвязанных изменений состояния."""
    item = db.scalar(
        select(ChangeRequest.id).where(
            ChangeRequest.id == change_id, ChangeRequest.organization_id == user.organization_id
        )
    )
    if not item:
        raise HTTPException(404, "Изменение не найдено")
    return list(
        db.scalars(
            select(DeploymentVerificationReport)
            .where(
                DeploymentVerificationReport.change_request_id == change_id,
                DeploymentVerificationReport.organization_id == user.organization_id,
            )
            .order_by(DeploymentVerificationReport.created_at.desc())
            .limit(100)
        ).all()
    )


@router.post(
    "/{change_id}/verify/{phase}", response_model=DeploymentVerificationRead, status_code=201
)
def verify_change(
    change_id: str,
    phase: DeploymentVerificationPhase,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Проверить change. Некорректные данные или состояние отклоняются до побочного эффекта."""
    item = db.scalar(
        select(ChangeRequest).where(
            ChangeRequest.id == change_id, ChangeRequest.organization_id == user.organization_id
        )
    )
    if not item:
        raise HTTPException(404, "Изменение не найдено")
    report = run_deployment_verification(db, change=item, phase=phase, user=user, settings=settings)
    write_audit(
        db,
        actor=user,
        action="change.verification",
        entity_type="deployment_verification_report",
        entity_id=report.id,
        details={"change_id": item.id, "phase": phase.value, "status": report.status.value},
        request=request,
    )
    db.commit()
    db.refresh(report)
    return report


@router.post("/{change_id}/start", response_model=ChangeRequestRead)
def start_change(
    change_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Выполнить операцию start change. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    item = db.scalar(
        select(ChangeRequest)
        .where(ChangeRequest.id == change_id, ChangeRequest.organization_id == user.organization_id)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Изменение не найдено")
    if item.status != ChangeRequestStatus.APPROVED:
        raise HTTPException(409, "Изменение не утверждено")
    org = db.get(Organization, user.organization_id)
    if org is None:
        raise HTTPException(404, "Организация не найдена")
    if (
        item.change_type
        in {
            ChangeRequestType.UPGRADE,
            ChangeRequestType.DATABASE_MIGRATION,
            ChangeRequestType.INFRASTRUCTURE,
        }
        and not org.maintenance_mode
    ):
        raise HTTPException(409, "Включите режим обслуживания")
    report = run_deployment_verification(
        db, change=item, phase=DeploymentVerificationPhase.PRE_CHANGE, user=user, settings=settings
    )
    if report.status == ReadinessStatus.BLOCKED:
        db.commit()
        raise HTTPException(
            409, "Pre-change проверка заблокирована: " + "; ".join(report.blockers[:3])
        )
    item.status = ChangeRequestStatus.IN_PROGRESS
    item.started_by_id = user.id
    item.started_at = utcnow()
    write_audit(
        db,
        actor=user,
        action="change.started",
        entity_type="change_request",
        entity_id=item.id,
        details={"verification_id": report.id},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/{change_id}/complete", response_model=ChangeRequestRead)
def complete_change(
    change_id: str,
    payload: ChangeActionRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Выполнить операцию complete change. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    item = db.scalar(
        select(ChangeRequest)
        .where(ChangeRequest.id == change_id, ChangeRequest.organization_id == user.organization_id)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Изменение не найдено")
    if item.status != ChangeRequestStatus.IN_PROGRESS:
        raise HTTPException(409, "Изменение не выполняется")
    report = run_deployment_verification(
        db, change=item, phase=DeploymentVerificationPhase.POST_CHANGE, user=user, settings=settings
    )
    if report.status == ReadinessStatus.BLOCKED:
        db.commit()
        raise HTTPException(
            409, "Post-change проверка заблокирована: " + "; ".join(report.blockers[:3])
        )
    item.status = ChangeRequestStatus.COMPLETED
    item.completed_by_id = user.id
    item.completed_at = utcnow()
    item.result_summary = {
        "note": payload.note,
        "verification_id": report.id,
        "verification_status": report.status.value,
    }
    write_audit(
        db,
        actor=user,
        action="change.completed",
        entity_type="change_request",
        entity_id=item.id,
        details=item.result_summary,
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/{change_id}/fail", response_model=ChangeRequestRead)
def fail_change(
    change_id: str,
    payload: ChangeActionRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Выполнить операцию fail change. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    item = db.scalar(
        select(ChangeRequest)
        .where(ChangeRequest.id == change_id, ChangeRequest.organization_id == user.organization_id)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Изменение не найдено")
    if item.status != ChangeRequestStatus.IN_PROGRESS:
        raise HTTPException(409, "Ошибка фиксируется только для выполняемого изменения")
    item.status = ChangeRequestStatus.FAILED
    item.completed_by_id = user.id
    item.completed_at = utcnow()
    item.result_summary = {"note": payload.note, "rollback_required": True}
    write_audit(
        db,
        actor=user,
        action="change.failed",
        entity_type="change_request",
        entity_id=item.id,
        details=item.result_summary,
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/{change_id}/cancel", response_model=ChangeRequestRead)
def cancel_change(
    change_id: str,
    payload: ChangeActionRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Безопасно выполнить cancel change. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    item = db.scalar(
        select(ChangeRequest)
        .where(ChangeRequest.id == change_id, ChangeRequest.organization_id == user.organization_id)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Изменение не найдено")
    if item.status not in {ChangeRequestStatus.DRAFT, ChangeRequestStatus.APPROVED}:
        raise HTTPException(409, "Текущее изменение нельзя отменить этим действием")
    item.status = ChangeRequestStatus.CANCELLED
    item.completed_by_id = user.id
    item.completed_at = utcnow()
    item.result_summary = {"note": payload.note}
    write_audit(
        db,
        actor=user,
        action="change.cancelled",
        entity_type="change_request",
        entity_id=item.id,
        details=item.result_summary,
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item
