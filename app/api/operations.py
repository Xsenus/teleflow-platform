from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_user, require_roles
from app.enums import (
    IncidentSource,
    IncidentStatus,
    SafetySeverity,
    SLOAssessmentSource,
    UserRole,
)
from app.models import Incident, IncidentEvent, SLOAssessment, User
from app.schemas import (
    IncidentActionRequest,
    IncidentCreate,
    IncidentEventRead,
    IncidentPatch,
    IncidentRead,
    OperationsOverviewRead,
    SLOAssessmentRead,
    SLOPolicyPatch,
    SLOPolicyRead,
)
from app.services.operations import (
    OperationsError,
    assessment_is_current,
    create_incident,
    evaluate_slo,
    get_or_create_slo_policy,
    latest_slo_assessment,
    slo_gate_decision,
    transition_incident,
    update_incident,
    update_slo_policy,
)

router = APIRouter(prefix="/operations", tags=["operations"])


def _incident_or_404(
    db: Session,
    *,
    incident_id: str,
    organization_id: str,
    for_update: bool = False,
) -> Incident:
    """Реализовать внутренний этап incident or 404 step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(Incident).where(
        Incident.id == incident_id,
        Incident.organization_id == organization_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if item is None:
        raise HTTPException(status_code=404, detail="Инцидент не найден")
    return item


@router.get("/overview", response_model=OperationsOverviewRead)
def get_operations_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Прочитать operations overview. Значение возвращается без несвязанных изменений состояния."""
    policy = get_or_create_slo_policy(
        db,
        organization_id=user.organization_id,
        settings=settings,
        user=user,
    )
    latest = latest_slo_assessment(db, organization_id=user.organization_id)
    publishing = slo_gate_decision(
        db,
        organization_id=user.organization_id,
        settings=settings,
        gate="publishing",
    )
    changes = slo_gate_decision(
        db,
        organization_id=user.organization_id,
        settings=settings,
        gate="changes",
    )
    active_statuses = [
        IncidentStatus.OPEN,
        IncidentStatus.ACKNOWLEDGED,
        IncidentStatus.MITIGATING,
    ]
    open_incidents = int(
        db.scalar(
            select(func.count(Incident.id)).where(
                Incident.organization_id == user.organization_id,
                Incident.status.in_(active_statuses),
            )
        )
        or 0
    )
    open_critical = int(
        db.scalar(
            select(func.count(Incident.id)).where(
                Incident.organization_id == user.organization_id,
                Incident.status.in_(active_statuses),
                Incident.severity == SafetySeverity.CRITICAL,
            )
        )
        or 0
    )
    requiring_attention = int(
        db.scalar(
            select(func.count(Incident.id)).where(
                Incident.organization_id == user.organization_id,
                Incident.status.in_([IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]),
            )
        )
        or 0
    )
    db.commit()
    db.refresh(policy)
    return OperationsOverviewRead(
        policy=SLOPolicyRead.model_validate(policy),
        latest_assessment=(
            SLOAssessmentRead.model_validate(latest) if latest is not None else None
        ),
        assessment_current=assessment_is_current(latest, policy),
        publishing_gate_allowed=publishing.allowed,
        publishing_gate_message=publishing.message,
        changes_gate_allowed=changes.allowed,
        changes_gate_message=changes.message,
        open_incidents=open_incidents,
        open_critical_incidents=open_critical,
        incidents_requiring_attention=requiring_attention,
    )


@router.get("/slo-policy", response_model=SLOPolicyRead)
def get_slo_policy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Прочитать slo policy. Значение возвращается без несвязанных изменений состояния."""
    item = get_or_create_slo_policy(
        db,
        organization_id=user.organization_id,
        settings=settings,
        user=user,
    )
    db.commit()
    db.refresh(item)
    return item


@router.patch("/slo-policy", response_model=SLOPolicyRead)
def patch_slo_policy(
    payload: SLOPolicyPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Обновить slo policy. Переход применяется только после проверки его предусловий."""
    item = get_or_create_slo_policy(
        db,
        organization_id=user.organization_id,
        settings=settings,
        user=user,
    )
    try:
        update_slo_policy(
            db,
            policy=item,
            values=payload.model_dump(exclude_unset=True),
            user=user,
            settings=settings,
        )
    except OperationsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user,
        action="slo.policy_updated",
        entity_type="slo_policy",
        entity_id=item.id,
        severity=SafetySeverity.WARNING,
        details={
            "enabled": item.enabled,
            "delivery_success_target_bps": item.delivery_success_target_bps,
            "gate_publishing": item.gate_publishing,
            "gate_changes": item.gate_changes,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.get("/slo-assessments", response_model=list[SLOAssessmentRead])
def list_slo_assessments(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать slo assessments. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(SLOAssessment)
            .where(SLOAssessment.organization_id == user.organization_id)
            .order_by(SLOAssessment.created_at.desc())
            .limit(limit)
        ).all()
    )


@router.post("/slo-assessments", response_model=SLOAssessmentRead, status_code=201)
def create_slo_assessment(
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Создать slo assessment. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    try:
        item = evaluate_slo(
            db,
            organization_id=user.organization_id,
            settings=settings,
            source=SLOAssessmentSource.MANUAL,
            user=user,
        )
    except OperationsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(item)
    return item


@router.get("/incidents", response_model=list[IncidentRead])
def list_incidents(
    status: IncidentStatus | None = Query(default=None),
    severity: SafetySeverity | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать incidents. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(Incident).where(Incident.organization_id == user.organization_id)
    if status is not None:
        stmt = stmt.where(Incident.status == status)
    if severity is not None:
        stmt = stmt.where(Incident.severity == severity)
    return list(db.scalars(stmt.order_by(Incident.detected_at.desc()).limit(limit)).all())


@router.post("/incidents", response_model=IncidentRead, status_code=201)
def create_manual_incident(
    payload: IncidentCreate,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Создать manual incident. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    try:
        item = create_incident(
            db,
            organization_id=user.organization_id,
            title=payload.title,
            summary=payload.summary,
            severity=payload.severity,
            # System-generated sources are reserved for trusted backend paths.
            # A browser/API operator can create only an explicitly manual event.
            source=IncidentSource.MANUAL,
            actor=user,
            settings=settings,
            impact=payload.impact,
            owner_user_id=payload.owner_user_id,
            started_at=payload.started_at,
        )
    except OperationsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(item)
    return item


@router.get("/incidents/{incident_id}", response_model=IncidentRead)
def get_incident(
    incident_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать incident. Значение возвращается без несвязанных изменений состояния."""
    return _incident_or_404(
        db,
        incident_id=incident_id,
        organization_id=user.organization_id,
    )


@router.patch("/incidents/{incident_id}", response_model=IncidentRead)
def patch_incident(
    incident_id: str,
    payload: IncidentPatch,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Обновить incident. Переход применяется только после проверки его предусловий."""
    item = _incident_or_404(
        db,
        incident_id=incident_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    values = payload.model_dump(exclude_unset=True)
    try:
        update_incident(db, incident=item, actor=user, **values)
    except OperationsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(item)
    return item


@router.get("/incidents/{incident_id}/events", response_model=list[IncidentEventRead])
def list_incident_events(
    incident_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать incident events. Значение возвращается без несвязанных изменений состояния."""
    _incident_or_404(
        db,
        incident_id=incident_id,
        organization_id=user.organization_id,
    )
    return list(
        db.scalars(
            select(IncidentEvent)
            .where(
                IncidentEvent.incident_id == incident_id,
                IncidentEvent.organization_id == user.organization_id,
            )
            .order_by(IncidentEvent.created_at.asc())
        ).all()
    )


def _transition(
    *,
    incident_id: str,
    action: str,
    payload: IncidentActionRequest,
    user: User,
    db: Session,
    settings: Settings,
) -> Incident:
    """Реализовать внутренний этап transition step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = _incident_or_404(
        db,
        incident_id=incident_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    try:
        transition_incident(
            db,
            incident=item,
            action=action,
            note=payload.note,
            actor=user,
            settings=settings,
            owner_user_id=payload.owner_user_id,
            root_cause=payload.root_cause,
            postmortem_url=payload.postmortem_url,
        )
    except OperationsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(item)
    return item


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentRead)
def acknowledge_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Выполнить операцию acknowledge incident. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return _transition(
        incident_id=incident_id,
        action="acknowledge",
        payload=payload,
        user=user,
        db=db,
        settings=settings,
    )


@router.post("/incidents/{incident_id}/mitigate", response_model=IncidentRead)
def mitigate_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Выполнить операцию mitigate incident. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return _transition(
        incident_id=incident_id,
        action="mitigate",
        payload=payload,
        user=user,
        db=db,
        settings=settings,
    )


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentRead)
def resolve_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Прочитать incident. Значение возвращается без несвязанных изменений состояния."""
    return _transition(
        incident_id=incident_id,
        action="resolve",
        payload=payload,
        user=user,
        db=db,
        settings=settings,
    )


@router.post("/incidents/{incident_id}/comment", response_model=IncidentRead)
def comment_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Выполнить операцию comment incident. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return _transition(
        incident_id=incident_id,
        action="comment",
        payload=payload,
        user=user,
        db=db,
        settings=settings,
    )


@router.post("/incidents/{incident_id}/close", response_model=IncidentRead)
def close_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Безопасно выполнить close incident. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    return _transition(
        incident_id=incident_id,
        action="close",
        payload=payload,
        user=user,
        db=db,
        settings=settings,
    )


@router.post("/incidents/{incident_id}/reopen", response_model=IncidentRead)
def reopen_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Выполнить операцию reopen incident. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    return _transition(
        incident_id=incident_id,
        action="reopen",
        payload=payload,
        user=user,
        db=db,
        settings=settings,
    )
