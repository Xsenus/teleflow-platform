from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_user, require_roles
from app.enums import (
    ApprovalDecision,
    CampaignApprovalStatus,
    CampaignStatus,
    CapacityAssessmentSource,
    ConnectionStatus,
    JobStatus,
    PermissionStatus,
    PreflightStatus,
    RolloutMode,
    RunStatus,
    SafetySeverity,
    ScheduleType,
    UserRole,
)
from app.models import (
    Campaign,
    CampaignApprovalRequest,
    CampaignDestination,
    CampaignPreflightReport,
    CampaignRun,
    DeliveryJob,
    Destination,
    MessageTemplate,
    Organization,
    TelegramConnection,
    User,
    utcnow,
)
from app.schemas import (
    CampaignApprovalDecisionRequest,
    CampaignApprovalRead,
    CampaignApprovalSubmitRequest,
    CampaignCreate,
    CampaignPatch,
    CampaignPreflightRead,
    CampaignPreviewItem,
    CampaignPreviewResponse,
    CampaignRead,
    CampaignRunRead,
    MessageResponse,
    RunAbortRequest,
    RunCheckpointRequest,
)
from app.security import aware_utc
from app.services.approvals import (
    approval_is_current,
    decide_approval_request,
    invalidate_campaign_approval,
    resolve_approval_policy,
    submit_approval_request,
)
from app.services.campaign_variants import campaign_variant_label, select_campaign_template
from app.services.capacity import CapacityError, capacity_admission_decision
from app.services.content_guard import content_fingerprint, find_recent_duplicate
from app.services.notifications import create_notification
from app.services.operations import slo_gate_decision
from app.services.pilot_stages import campaign_stage_blocker
from app.services.preflight import (
    create_preflight_report,
    latest_preflight_report,
    preflight_is_current,
)
from app.services.readiness import latest_readiness_report, readiness_is_current
from app.services.rollouts import batch_health, cancel_run_jobs, release_next_batch
from app.services.safety import validate_campaign
from app.services.scheduler import campaign_capacity_projection

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class ReplaceDestinationsRequest(BaseModel):
    destination_ids: list[str] = Field(min_length=1, max_length=500)


def _as_utc(value: datetime, timezone_name: str) -> datetime:
    """Реализовать внутренний этап as utc step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Неизвестный часовой пояс") from exc
    return value.replace(tzinfo=zone).astimezone(UTC)


def _validate_zone(name: str) -> None:
    """Реализовать внутренний этап validate zone step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Неизвестный часовой пояс") from exc


def _require_publishing_slo_gate(db: Session, *, organization_id: str, settings: Settings):
    """Реализовать внутренний этап require publishing slo gate step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    decision = slo_gate_decision(
        db,
        organization_id=organization_id,
        settings=settings,
        gate="publishing",
    )
    if not decision.allowed:
        raise HTTPException(status_code=409, detail=decision.message)
    return decision


def _campaign_stmt() -> Select[tuple[Campaign]]:
    """Реализовать внутренний этап campaign stmt step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return select(Campaign).options(
        selectinload(Campaign.connection),
        selectinload(Campaign.template),
        selectinload(Campaign.secondary_template),
        selectinload(Campaign.destinations).selectinload(CampaignDestination.destination),
        selectinload(Campaign.approval_requests).selectinload(CampaignApprovalRequest.decisions),
    )


def _campaign_read(campaign: Campaign) -> CampaignRead:
    """Реализовать внутренний этап campaign read step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    data = CampaignRead.model_validate(campaign)
    enabled_links = [link for link in campaign.destinations if link.enabled]
    data.destination_count = len(enabled_links)
    data.destination_ids = [link.destination_id for link in enabled_links]
    active = next(
        (
            item
            for item in campaign.approval_requests
            if item.id == campaign.active_approval_request_id
        ),
        None,
    )
    if active:
        data.approval_status = active.status
        data.approval_required = active.required_approvals
        data.approval_received = len(
            [item for item in active.decisions if item.decision == ApprovalDecision.APPROVE]
        )
        data.approval_expires_at = active.expires_at
    return data


def _load_campaign(
    db: Session,
    campaign_id: str,
    organization_id: str,
    *,
    for_update: bool = False,
) -> Campaign:
    """Реализовать внутренний этап load campaign step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = (
        _campaign_stmt()
        .where(Campaign.id == campaign_id, Campaign.organization_id == organization_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    campaign = db.scalar(stmt)
    if not campaign:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return campaign


def _load_approval_request(
    db: Session,
    request_id: str,
    organization_id: str,
    *,
    for_update: bool = False,
) -> CampaignApprovalRequest:
    """Реализовать внутренний этап load approval request step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = (
        select(CampaignApprovalRequest)
        .options(selectinload(CampaignApprovalRequest.decisions))
        .execution_options(populate_existing=True)
        .where(
            CampaignApprovalRequest.id == request_id,
            CampaignApprovalRequest.organization_id == organization_id,
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if not item:
        raise HTTPException(status_code=404, detail="Запрос на утверждение не найден")
    return item


def _ensure_approval_capacity(
    db: Session,
    *,
    campaign: Campaign,
    organization: Organization,
    requester: User,
) -> None:
    """Отклонить governance policies that current privileged users cannot satisfy."""

    policy = resolve_approval_policy(campaign, organization)
    eligible_query = select(func.count(User.id)).where(
        User.organization_id == requester.organization_id,
        User.is_active.is_(True),
        User.role.in_([UserRole.OWNER, UserRole.ADMIN]),
    )
    if policy.require_distinct_requester:
        eligible_query = eligible_query.where(User.id != requester.id)
    eligible = int(db.scalar(eligible_query) or 0)
    if eligible < policy.required_approvals:
        raise HTTPException(
            status_code=409,
            detail=(
                "Недостаточно независимых владельцев/администраторов для выбранной политики: "
                f"нужно {policy.required_approvals}, доступно {eligible}"
            ),
        )


def _ensure_no_uncertain_deliveries(
    db: Session,
    *,
    organization_id: str,
    campaign_id: str,
    run_id: str | None = None,
) -> None:
    """Реализовать внутренний этап ensure no uncertain deliveries step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    stmt = select(func.count(DeliveryJob.id)).where(
        DeliveryJob.organization_id == organization_id,
        DeliveryJob.campaign_id == campaign_id,
        DeliveryJob.status == JobStatus.WAITING_REVIEW,
        DeliveryJob.error_code.in_(["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"]),
    )
    if run_id is not None:
        stmt = stmt.where(DeliveryJob.run_id == run_id)
    count = int(db.scalar(stmt) or 0)
    if count:
        raise HTTPException(
            status_code=409,
            detail=(
                "Есть неоднозначные результаты доставки. Сначала вручную сверите "
                "целевые чаты и зафиксируйте результат каждого задания."
            ),
        )


def _cancel_nonterminal_campaign_work(db: Session, campaign: Campaign, *, code: str) -> int:
    """Реализовать внутренний этап cancel nonterminal campaign work step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    _ensure_no_uncertain_deliveries(
        db,
        organization_id=campaign.organization_id,
        campaign_id=campaign.id,
    )
    now = utcnow()
    result = db.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.campaign_id == campaign.id,
            DeliveryJob.organization_id == campaign.organization_id,
            DeliveryJob.status.in_(
                [JobStatus.HELD, JobStatus.PENDING, JobStatus.RETRY, JobStatus.WAITING_REVIEW]
            ),
        )
        .values(
            status=JobStatus.CANCELLED,
            finished_at=now,
            error_code=code,
            error_message="Задание отменено после изменения кампании",
            locked_at=None,
            locked_by=None,
        )
    )
    runs = list(
        db.scalars(
            select(CampaignRun).where(
                CampaignRun.campaign_id == campaign.id,
                CampaignRun.organization_id == campaign.organization_id,
                CampaignRun.status.in_(
                    [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_CHECKPOINT]
                ),
            )
        ).all()
    )
    for run in runs:
        run.status = RunStatus.CANCELLED
        run.finished_at = now
    return int(getattr(result, "rowcount", 0) or 0)


def _load_run(
    db: Session,
    *,
    campaign_id: str,
    run_id: str,
    organization_id: str,
    for_update: bool = False,
) -> CampaignRun:
    """Реализовать внутренний этап load run step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(CampaignRun).where(
        CampaignRun.id == run_id,
        CampaignRun.campaign_id == campaign_id,
        CampaignRun.organization_id == organization_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    run = db.scalar(stmt)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск кампании не найден")
    return run


def _run_preflight(
    db: Session,
    *,
    campaign: Campaign,
    user: User,
    settings: Settings,
    request: Request,
    purpose: str,
) -> CampaignPreflightReport:
    """Реализовать внутренний этап run preflight step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    report = create_preflight_report(
        db,
        campaign=campaign,
        created_by=user,
        settings=settings,
        storage=request.app.state.storage,
    )
    severity = (
        SafetySeverity.CRITICAL
        if report.status == PreflightStatus.BLOCKED
        else SafetySeverity.WARNING
        if report.status == PreflightStatus.WARNING
        else SafetySeverity.INFO
    )
    write_audit(
        db,
        actor=user,
        action="campaign.preflight_completed",
        entity_type="campaign_preflight_report",
        entity_id=report.id,
        severity=severity,
        details={
            "campaign_id": campaign.id,
            "purpose": purpose,
            "status": report.status.value,
            "blockers": report.blockers,
            "warnings": report.warnings,
            "expires_at": report.expires_at.isoformat(),
        },
        request=request,
    )
    if report.status == PreflightStatus.BLOCKED:
        # Keep the failed report and its audit record so the operator can see
        # exactly why the launch was refused.
        db.commit()
        raise HTTPException(
            status_code=422,
            detail={
                "preflight_report_id": report.id,
                "blockers": report.blockers,
                "warnings": report.warnings,
            },
        )
    return report


@router.get("", response_model=list[CampaignRead])
def list_campaigns(
    status_filter: CampaignStatus | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CampaignRead]:
    """Прочитать campaigns. Значение возвращается без несвязанных изменений состояния."""
    stmt = (
        _campaign_stmt()
        .where(Campaign.organization_id == _user.organization_id)
        .order_by(Campaign.created_at.desc())
    )
    if status_filter:
        stmt = stmt.where(Campaign.status == status_filter)
    campaigns = list(db.scalars(stmt).unique().all())
    return [_campaign_read(item) for item in campaigns]


@router.get("/approval-requests", response_model=list[CampaignApprovalRead])
def list_approval_requests(
    status_filter: CampaignApprovalStatus | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CampaignApprovalRequest]:
    """Прочитать approval requests. Значение возвращается без несвязанных изменений состояния."""
    stmt = (
        select(CampaignApprovalRequest)
        .options(selectinload(CampaignApprovalRequest.decisions))
        .where(CampaignApprovalRequest.organization_id == user.organization_id)
        .order_by(CampaignApprovalRequest.created_at.desc())
    )
    if status_filter:
        stmt = stmt.where(CampaignApprovalRequest.status == status_filter)
    # Expiration is a state transition with audit/notification side effects and is
    # therefore performed by the scheduler, never by this read-only endpoint.
    return list(db.scalars(stmt).unique().all())


@router.post(
    "/approval-requests/{request_id}/decision",
    response_model=CampaignApprovalRead,
)
def decide_campaign_approval(
    request_id: str,
    payload: CampaignApprovalDecisionRequest,
    http_request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignApprovalRequest:
    """Выполнить операцию decide campaign approval. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    approval_request = _load_approval_request(db, request_id, user.organization_id, for_update=True)
    campaign = _load_campaign(
        db, approval_request.campaign_id, user.organization_id, for_update=True
    )
    approval_request, decision = decide_approval_request(
        db,
        request=approval_request,
        campaign=campaign,
        user=user,
        decision=payload.decision,
        note=payload.note,
    )
    action = (
        "campaign.approval_rejected"
        if payload.decision == ApprovalDecision.REJECT
        else "campaign.approval_recorded"
    )
    write_audit(
        db,
        actor=user,
        action=action,
        entity_type="campaign_approval_request",
        entity_id=approval_request.id,
        details={
            "campaign_id": campaign.id,
            "decision": payload.decision.value,
            "status": approval_request.status.value,
            "required_approvals": approval_request.required_approvals,
        },
        request=http_request,
    )
    if approval_request.status == CampaignApprovalStatus.APPROVED:
        title = "Кампания утверждена"
        message = f"Кампания «{campaign.name}» получила все необходимые утверждения."
        severity = SafetySeverity.INFO
    elif approval_request.status == CampaignApprovalStatus.REJECTED:
        title = "Кампания отклонена"
        message = f"Кампания «{campaign.name}» отклонена пользователем {user.display_name}."
        severity = SafetySeverity.WARNING
    else:
        title = "Получено утверждение кампании"
        message = f"{user.display_name} подтвердил кампанию «{campaign.name}»."
        severity = SafetySeverity.INFO
    create_notification(
        db,
        settings=settings,
        organization_id=user.organization_id,
        event_type=action,
        title=title,
        message=message,
        severity=severity,
        entity_type="campaign",
        entity_id=campaign.id,
        dedup_key=f"{action}:{approval_request.id}:{decision.user_id}",
        details={
            "approval_request_id": approval_request.id,
            "status": approval_request.status.value,
        },
    )
    db.commit()
    return _load_approval_request(db, approval_request.id, user.organization_id)


@router.post("", response_model=CampaignRead, status_code=201)
def create_campaign(
    payload: CampaignCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignRead:
    """Создать campaign. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    _validate_zone(payload.timezone_name)
    connection = db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.id == payload.connection_id,
            TelegramConnection.organization_id == user.organization_id,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Telegram-подключение не найдено")
    if connection.status == ConnectionStatus.REVOKED:
        raise HTTPException(status_code=409, detail="Подключение отозвано")
    template = db.scalar(
        select(MessageTemplate).where(
            MessageTemplate.id == payload.template_id,
            MessageTemplate.organization_id == user.organization_id,
        )
    )
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    secondary_template = None
    if payload.secondary_template_id:
        secondary_template = db.scalar(
            select(MessageTemplate).where(
                MessageTemplate.id == payload.secondary_template_id,
                MessageTemplate.organization_id == user.organization_id,
            )
        )
        if not secondary_template:
            raise HTTPException(status_code=404, detail="Шаблон варианта B не найден")
        if secondary_template.id == template.id:
            raise HTTPException(status_code=422, detail="Варианты A и B должны отличаться")
    destinations = list(
        db.scalars(
            select(Destination).where(
                Destination.id.in_(payload.destination_ids),
                Destination.organization_id == user.organization_id,
            )
        ).all()
    )
    by_id = {item.id: item for item in destinations}
    if len(by_id) != len(payload.destination_ids):
        raise HTTPException(status_code=404, detail="Одно или несколько назначений не найдены")
    wrong_connection = [item.title for item in destinations if item.connection_id != connection.id]
    if wrong_connection:
        raise HTTPException(
            status_code=422,
            detail=f"Назначения относятся к другому подключению: {', '.join(wrong_connection)}",
        )
    schedule_at = _as_utc(payload.schedule_at, payload.timezone_name)
    end_at = _as_utc(payload.end_at, payload.timezone_name) if payload.end_at else None
    if end_at and end_at <= schedule_at:
        raise HTTPException(status_code=422, detail="Окончание должно быть позже начала")
    campaign = Campaign(
        organization_id=user.organization_id,
        name=payload.name,
        connection_id=connection.id,
        template_id=template.id,
        secondary_template_id=secondary_template.id if secondary_template else None,
        secondary_template_weight=payload.secondary_template_weight if secondary_template else 0,
        status=CampaignStatus.DRAFT,
        schedule_type=payload.schedule_type,
        schedule_at=schedule_at,
        timezone_name=payload.timezone_name,
        weekdays=sorted(set(payload.weekdays)),
        spacing_seconds=payload.spacing_seconds,
        rollout_mode=payload.rollout_mode,
        rollout_batch_size=payload.rollout_batch_size,
        rollout_pause_seconds=payload.rollout_pause_seconds,
        rollout_require_checkpoint=payload.rollout_require_checkpoint,
        rollout_failure_threshold_percent=payload.rollout_failure_threshold_percent,
        duplicate_guard_minutes=payload.duplicate_guard_minutes,
        end_at=end_at,
        manual_approval_required=True,
        created_by_id=user.id,
        notes=payload.notes,
    )
    db.add(campaign)
    db.flush()
    for position, destination_id in enumerate(payload.destination_ids):
        db.add(
            CampaignDestination(
                organization_id=user.organization_id,
                campaign_id=campaign.id,
                destination_id=destination_id,
                position=position,
                enabled=True,
            )
        )
    invalidate_campaign_approval(db, campaign, reason="маршрут кампании изменён")
    campaign.status = CampaignStatus.DRAFT
    write_audit(
        db,
        action="campaign.created",
        actor=user,
        entity_type="campaign",
        entity_id=campaign.id,
        details={
            "connection_id": connection.id,
            "template_id": template.id,
            "secondary_template_id": secondary_template.id if secondary_template else None,
            "secondary_template_weight": payload.secondary_template_weight
            if secondary_template
            else 0,
            "destination_count": len(payload.destination_ids),
            "schedule_type": payload.schedule_type.value,
            "rollout_mode": payload.rollout_mode.value,
            "rollout_batch_size": payload.rollout_batch_size,
            "duplicate_guard_minutes": payload.duplicate_guard_minutes,
        },
        request=request,
    )
    db.commit()
    return _campaign_read(_load_campaign(db, campaign.id, user.organization_id))


@router.get("/{campaign_id}", response_model=CampaignRead)
def get_campaign(
    campaign_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignRead:
    """Прочитать campaign. Значение возвращается без несвязанных изменений состояния."""
    return _campaign_read(_load_campaign(db, campaign_id, _user.organization_id))


@router.patch("/{campaign_id}", response_model=CampaignRead)
def patch_campaign(
    campaign_id: str,
    payload: CampaignPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> CampaignRead:
    """Обновить campaign. Переход применяется только после проверки его предусловий."""
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    if campaign.status not in {CampaignStatus.DRAFT, CampaignStatus.PAUSED}:
        raise HTTPException(status_code=409, detail="Редактировать можно черновик или паузу")
    data = payload.model_dump(exclude_unset=True)
    resulting_secondary_id = data.get("secondary_template_id", campaign.secondary_template_id)
    resulting_secondary_weight = data.get(
        "secondary_template_weight", campaign.secondary_template_weight
    )
    if resulting_secondary_id:
        if resulting_secondary_id == campaign.template_id:
            raise HTTPException(status_code=422, detail="Варианты A и B должны отличаться")
        secondary_template = db.scalar(
            select(MessageTemplate).where(
                MessageTemplate.id == resulting_secondary_id,
                MessageTemplate.organization_id == user.organization_id,
            )
        )
        if not secondary_template:
            raise HTTPException(status_code=404, detail="Шаблон варианта B не найден")
        if not 1 <= int(resulting_secondary_weight or 0) <= 99:
            raise HTTPException(status_code=422, detail="Вес варианта B должен быть от 1 до 99")
    else:
        if resulting_secondary_weight not in {None, 0}:
            raise HTTPException(
                status_code=422,
                detail="Вес варианта B задаётся только вместе со вторым шаблоном",
            )
        if "secondary_template_id" in data:
            data["secondary_template_weight"] = 0
    timezone_name = data.get("timezone_name", campaign.timezone_name)
    _validate_zone(timezone_name)
    if data.get("schedule_at") is not None:
        data["schedule_at"] = _as_utc(data["schedule_at"], timezone_name)
    if data.get("end_at") is not None:
        data["end_at"] = _as_utc(data["end_at"], timezone_name)
    if data.get("weekdays") is not None:
        days = sorted(set(data["weekdays"]))
        if any(day < 0 or day > 6 for day in days):
            raise HTTPException(status_code=422, detail="Дни недели должны быть 0..6")
        data["weekdays"] = days
    resulting_schedule_type = data.get("schedule_type", campaign.schedule_type)
    resulting_weekdays = data.get("weekdays", campaign.weekdays)
    if resulting_schedule_type == ScheduleType.WEEKLY and not resulting_weekdays:
        raise HTTPException(status_code=422, detail="Для еженедельного расписания выберите дни")
    for key, value in data.items():
        setattr(campaign, key, value)
    normalized_end = aware_utc(campaign.end_at)
    normalized_schedule = aware_utc(campaign.schedule_at)
    if (
        normalized_end is not None
        and normalized_schedule is not None
        and normalized_end <= normalized_schedule
    ):
        raise HTTPException(status_code=422, detail="Окончание должно быть позже начала")
    cancelled_jobs = _cancel_nonterminal_campaign_work(db, campaign, code="CAMPAIGN_EDITED")
    invalidate_campaign_approval(db, campaign, reason="кампания изменена")
    campaign.status = CampaignStatus.DRAFT
    write_audit(
        db,
        action="campaign.updated",
        actor=user,
        entity_type="campaign",
        entity_id=campaign.id,
        details={"fields": sorted(data), "cancelled_jobs": cancelled_jobs},
        request=request,
    )
    db.commit()
    return _campaign_read(_load_campaign(db, campaign.id, user.organization_id))


@router.put("/{campaign_id}/destinations", response_model=CampaignRead)
def replace_destinations(
    campaign_id: str,
    payload: ReplaceDestinationsRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> CampaignRead:
    """Выполнить операцию replace destinations. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    if campaign.status != CampaignStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Состав назначений меняется только в черновике")
    if len(set(payload.destination_ids)) != len(payload.destination_ids):
        raise HTTPException(status_code=422, detail="Список содержит дубликаты")
    destinations = list(
        db.scalars(
            select(Destination).where(
                Destination.id.in_(payload.destination_ids),
                Destination.organization_id == user.organization_id,
            )
        ).all()
    )
    if len(destinations) != len(payload.destination_ids):
        raise HTTPException(status_code=404, detail="Одно или несколько назначений не найдены")
    if any(item.connection_id != campaign.connection_id for item in destinations):
        raise HTTPException(
            status_code=422, detail="Все назначения должны использовать подключение кампании"
        )
    for link in list(campaign.destinations):
        db.delete(link)
    db.flush()
    for position, destination_id in enumerate(payload.destination_ids):
        db.add(
            CampaignDestination(
                organization_id=user.organization_id,
                campaign_id=campaign.id,
                destination_id=destination_id,
                position=position,
            )
        )
    invalidate_campaign_approval(db, campaign, reason="маршрут кампании изменён")
    write_audit(
        db,
        action="campaign.destinations_replaced",
        actor=user,
        entity_type="campaign",
        entity_id=campaign.id,
        details={"destination_count": len(payload.destination_ids)},
        request=request,
    )
    db.commit()
    return _campaign_read(_load_campaign(db, campaign.id, user.organization_id))


@router.get("/{campaign_id}/preview", response_model=CampaignPreviewResponse)
def preview_campaign(
    campaign_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignPreviewResponse:
    """Выполнить операцию preview campaign. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    campaign = _load_campaign(db, campaign_id, _user.organization_id)
    blockers, warnings = validate_campaign(campaign, settings)
    organization = db.get(Organization, _user.organization_id)
    stage_blocker = (
        campaign_stage_blocker(organization, campaign, settings)
        if organization
        else "Организация кампании не найдена"
    )
    if stage_blocker:
        blockers.append(stage_blocker)
    now = utcnow()
    start_at = aware_utc(campaign.next_run_at) or aware_utc(campaign.schedule_at) or now
    if start_at < now:
        warnings.append(
            "Время старта уже наступило; после утверждения запуск будет поставлен на ближайший tick"
        )
        start_at = now
    items: list[CampaignPreviewItem] = []
    active_links = [
        item
        for item in sorted(campaign.destinations, key=lambda link: (link.position, link.id))
        if item.enabled
    ]
    batch_size = max(1, campaign.rollout_batch_size)
    for position, link in enumerate(active_links):
        destination = link.destination
        selected_template = select_campaign_template(campaign, destination.id)
        body = link.custom_body or selected_template.body
        media_sha = (
            selected_template.media_asset.sha256
            if selected_template.media_asset is not None
            else None
        )
        fingerprint = content_fingerprint(
            body=body,
            parse_mode=selected_template.parse_mode,
            media_sha256=media_sha,
            link_preview=selected_template.link_preview,
        )
        item_blockers: list[str] = []
        item_warnings: list[str] = []
        if not destination.enabled:
            item_blockers.append("Назначение отключено")
        if destination.permission_status != PermissionStatus.CONFIRMED:
            item_blockers.append("Разрешение на публикацию не подтверждено")
        expires_at = aware_utc(destination.permission_expires_at)
        if expires_at and expires_at <= now:
            item_blockers.append("Срок подтверждённого разрешения истёк")
        duplicate = find_recent_duplicate(
            db,
            organization_id=campaign.organization_id,
            destination_id=destination.id,
            fingerprint=fingerprint,
            lookback_minutes=campaign.duplicate_guard_minutes,
            now=now,
        )
        if duplicate:
            item_blockers.append("Такой же снимок уже отправлялся в период duplicate guard")
        for text in item_blockers:
            value = f"{destination.title}: {text}"
            if value not in blockers:
                blockers.append(value)
        for text in item_warnings:
            value = f"{destination.title}: {text}"
            if value not in warnings:
                warnings.append(value)
        batch_number = (
            (position // batch_size) + 1 if campaign.rollout_mode == RolloutMode.STAGED else 1
        )
        items.append(
            CampaignPreviewItem(
                destination_id=destination.id,
                destination_title=destination.title,
                due_at=start_at + timedelta(seconds=position * campaign.spacing_seconds),
                template_id=selected_template.id,
                template_name=selected_template.name,
                template_variant=campaign_variant_label(campaign, selected_template.id),
                body=body,
                permission_status=destination.permission_status,
                enabled=destination.enabled,
                batch_number=batch_number,
                content_fingerprint=fingerprint,
                blockers=item_blockers,
                warnings=item_warnings,
            )
        )
    return CampaignPreviewResponse(
        campaign_id=campaign.id,
        valid=not blockers,
        blockers=list(dict.fromkeys(blockers)),
        warnings=list(dict.fromkeys(warnings)),
        items=items,
    )


@router.post("/{campaign_id}/preflight", response_model=CampaignPreflightRead)
def run_campaign_preflight(
    campaign_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignPreflightReport:
    """Выполнить run campaign preflight. Операция координирует ограниченные побочные эффекты и
    возвращает детерминированный результат.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    report = _run_preflight(
        db,
        campaign=campaign,
        user=user,
        settings=settings,
        request=request,
        purpose="manual",
    )
    db.commit()
    db.refresh(report)
    return report


@router.get("/{campaign_id}/preflight/latest", response_model=CampaignPreflightRead)
def get_latest_campaign_preflight(
    campaign_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignPreflightReport:
    """Прочитать latest campaign preflight. Значение возвращается без несвязанных изменений
    состояния.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id)
    report = latest_preflight_report(db, campaign=campaign)
    if not report:
        raise HTTPException(status_code=404, detail="Предзапусковая проверка ещё не выполнялась")
    expires_at = aware_utc(report.expires_at)
    if (
        report.status in {PreflightStatus.PASSED, PreflightStatus.WARNING}
        and expires_at
        and expires_at <= utcnow()
    ):
        report.status = PreflightStatus.EXPIRED
        db.commit()
        db.refresh(report)
    return report


@router.get("/{campaign_id}/approval", response_model=CampaignApprovalRead)
def get_campaign_approval(
    campaign_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignApprovalRequest:
    """Прочитать campaign approval. Значение возвращается без несвязанных изменений состояния."""
    campaign = _load_campaign(db, campaign_id, user.organization_id)
    if not campaign.active_approval_request_id:
        latest = db.scalar(
            select(CampaignApprovalRequest)
            .options(selectinload(CampaignApprovalRequest.decisions))
            .where(
                CampaignApprovalRequest.campaign_id == campaign.id,
                CampaignApprovalRequest.organization_id == user.organization_id,
            )
            .order_by(CampaignApprovalRequest.created_at.desc())
            .limit(1)
        )
        if not latest:
            raise HTTPException(status_code=404, detail="Запросов на утверждение ещё нет")
        return latest
    # The scheduler owns expiration so that every transition is audited and
    # produces the same operator notification regardless of which UI is open.
    return _load_approval_request(db, campaign.active_approval_request_id, user.organization_id)


@router.post("/{campaign_id}/submit-approval", response_model=CampaignApprovalRead)
def submit_campaign_approval(
    campaign_id: str,
    payload: CampaignApprovalSubmitRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignApprovalRequest:
    """Выполнить операцию submit campaign approval. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    if campaign.status not in {CampaignStatus.DRAFT, CampaignStatus.PAUSED}:
        raise HTTPException(
            status_code=409,
            detail="На утверждение отправляется только черновик или кампания на паузе",
        )
    preflight = _run_preflight(
        db,
        campaign=campaign,
        user=user,
        settings=settings,
        request=request,
        purpose="approval_request",
    )
    warnings = list(preflight.warnings)
    organization = db.get(Organization, user.organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    _ensure_approval_capacity(
        db,
        campaign=campaign,
        organization=organization,
        requester=user,
    )
    item = submit_approval_request(
        db,
        campaign=campaign,
        organization=organization,
        requested_by=user,
        note=payload.note,
    )
    write_audit(
        db,
        actor=user,
        action="campaign.approval_requested",
        entity_type="campaign_approval_request",
        entity_id=item.id,
        details={
            "campaign_id": campaign.id,
            "required_approvals": item.required_approvals,
            "require_distinct_requester": item.require_distinct_requester,
            "expires_at": item.expires_at.isoformat(),
            "warnings": warnings,
            "preflight_report_id": preflight.id,
        },
        request=request,
    )
    create_notification(
        db,
        settings=settings,
        organization_id=user.organization_id,
        event_type="campaign.approval_requested",
        title="Кампания ожидает утверждения",
        message=(
            f"{user.display_name} отправил кампанию «{campaign.name}» на утверждение. "
            f"Требуется решений: {item.required_approvals}."
        ),
        severity=SafetySeverity.WARNING,
        entity_type="campaign",
        entity_id=campaign.id,
        dedup_key=f"campaign-approval-requested:{item.id}",
        details={"approval_request_id": item.id},
    )
    db.commit()
    return _load_approval_request(db, item.id, user.organization_id)


@router.post("/{campaign_id}/approve", response_model=CampaignRead)
def approve_campaign(
    campaign_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignRead:
    """Совместимая точка API backed by the explicit approval workflow. In simple mode the
    administrator submits and approves in one transaction. When four-eyes mode is enabled the
    endpoint creates a pending request and requires a different administrator to decide it.
    """

    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    if campaign.status not in {CampaignStatus.DRAFT, CampaignStatus.PAUSED}:
        raise HTTPException(status_code=409, detail="Кампания уже утверждена или завершена")
    preflight = _run_preflight(
        db,
        campaign=campaign,
        user=user,
        settings=settings,
        request=request,
        purpose="compatibility_approval",
    )
    warnings = list(preflight.warnings)
    organization = db.get(Organization, user.organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    _ensure_approval_capacity(
        db,
        campaign=campaign,
        organization=organization,
        requester=user,
    )

    approval_request = submit_approval_request(
        db,
        campaign=campaign,
        organization=organization,
        requested_by=user,
        note="Быстрое утверждение через совместимый endpoint",
    )
    write_audit(
        db,
        action="campaign.approval_requested",
        actor=user,
        entity_type="campaign_approval_request",
        entity_id=approval_request.id,
        details={
            "campaign_id": campaign.id,
            "required_approvals": approval_request.required_approvals,
            "compatibility_endpoint": True,
            "warnings": warnings,
            "preflight_report_id": preflight.id,
        },
        request=request,
    )

    if approval_request.require_distinct_requester:
        create_notification(
            db,
            settings=settings,
            organization_id=user.organization_id,
            event_type="campaign.approval_requested",
            title="Нужно независимое утверждение кампании",
            message=(
                f"Кампания «{campaign.name}» отправлена на утверждение. "
                "Автор запроса не может подтвердить её самостоятельно."
            ),
            severity=SafetySeverity.WARNING,
            entity_type="campaign",
            entity_id=campaign.id,
            dedup_key=f"campaign-approval-requested:{approval_request.id}",
        )
        db.commit()
        return _campaign_read(_load_campaign(db, campaign.id, user.organization_id))

    approval_request, _decision = decide_approval_request(
        db,
        request=approval_request,
        campaign=campaign,
        user=user,
        decision=ApprovalDecision.APPROVE,
        note="Утверждено через совместимый endpoint",
    )
    write_audit(
        db,
        action="campaign.approved"
        if approval_request.status == CampaignApprovalStatus.APPROVED
        else "campaign.approval_recorded",
        actor=user,
        entity_type="campaign",
        entity_id=campaign.id,
        details={
            "approval_request_id": approval_request.id,
            "status": approval_request.status.value,
            "required_approvals": approval_request.required_approvals,
            "next_run_at": campaign.next_run_at.isoformat() if campaign.next_run_at else None,
        },
        request=request,
    )
    if approval_request.status == CampaignApprovalStatus.PENDING:
        received = sum(
            1 for item in approval_request.decisions if item.decision == ApprovalDecision.APPROVE
        )
        create_notification(
            db,
            settings=settings,
            organization_id=user.organization_id,
            event_type="campaign.approval_recorded",
            title="Нужны дополнительные утверждения",
            message=(
                f"Кампания «{campaign.name}» получила {received} из "
                f"{approval_request.required_approvals} необходимых решений."
            ),
            severity=SafetySeverity.WARNING,
            entity_type="campaign",
            entity_id=campaign.id,
            dedup_key=f"campaign-approval-progress:{approval_request.id}:{received}",
            details={
                "approval_request_id": approval_request.id,
                "received": received,
                "required": approval_request.required_approvals,
            },
        )
    db.commit()
    return _campaign_read(_load_campaign(db, campaign.id, user.organization_id))


@router.post("/{campaign_id}/run-now", response_model=CampaignRead)
def run_now(
    campaign_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignRead:
    """Выполнить run now. Операция координирует ограниченные побочные эффекты и возвращает
    детерминированный результат.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    if campaign.status in {
        CampaignStatus.CANCELLED,
        CampaignStatus.COMPLETED,
        CampaignStatus.FAILED,
    }:
        raise HTTPException(
            status_code=409, detail="Завершённую кампанию нельзя запустить повторно"
        )
    if not approval_is_current(campaign):
        raise HTTPException(
            status_code=409,
            detail="Сначала получите актуальное утверждение кампании",
        )
    organization = db.get(Organization, user.organization_id)
    stage_blocker = (
        campaign_stage_blocker(organization, campaign, settings)
        if organization
        else "Организация кампании не найдена"
    )
    if stage_blocker:
        raise HTTPException(status_code=409, detail=stage_blocker)
    if organization and organization.publishing_paused:
        raise HTTPException(
            status_code=409,
            detail=organization.publishing_pause_reason
            or "Публикации остановлены на уровне организации",
        )
    if organization and organization.maintenance_mode:
        raise HTTPException(
            status_code=409,
            detail=organization.maintenance_reason or "Платформа находится в режиме обслуживания",
        )
    slo_gate = _require_publishing_slo_gate(
        db, organization_id=user.organization_id, settings=settings
    )
    readiness = latest_readiness_report(db, campaign=campaign)
    if settings.pilot_readiness_required and not readiness_is_current(readiness, campaign):
        raise HTTPException(
            status_code=409,
            detail=(
                "Создайте актуальный отчёт готовности в разделе «Пилот» после утверждения кампании"
            ),
        )
    active_runs = int(
        db.scalar(
            select(func.count(CampaignRun.id)).where(
                CampaignRun.organization_id == user.organization_id,
                CampaignRun.campaign_id == campaign.id,
                CampaignRun.status.in_(
                    [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_CHECKPOINT]
                ),
            )
        )
        or 0
    )
    if active_runs:
        raise HTTPException(
            status_code=409,
            detail="У кампании уже есть незавершённый запуск",
        )
    preflight = _run_preflight(
        db,
        campaign=campaign,
        user=user,
        settings=settings,
        request=request,
        purpose="run_now",
    )
    if not preflight_is_current(preflight, campaign):
        raise HTTPException(status_code=409, detail="Предзапусковая проверка устарела")
    total_jobs, ready_jobs, due_span_seconds = campaign_capacity_projection(campaign)
    capacity = capacity_admission_decision(
        db,
        organization_id=user.organization_id,
        settings=settings,
        incoming_jobs=total_jobs,
        incoming_ready_jobs=ready_jobs,
        incoming_runs=1,
        incoming_connection_id=campaign.connection_id,
        incoming_due_span_seconds=due_span_seconds,
        source=CapacityAssessmentSource.ADMISSION,
        user=user,
        persist_assessment=True,
    )
    if not capacity.allowed:
        raise HTTPException(status_code=409, detail=capacity.message)
    now = utcnow()
    campaign.status = CampaignStatus.SCHEDULED
    campaign.next_run_at = now
    write_audit(
        db,
        action="campaign.run_now_requested",
        actor=user,
        entity_type="campaign",
        entity_id=campaign.id,
        details={
            "preflight_report_id": preflight.id,
            "preflight_status": preflight.status.value,
            "warnings": preflight.warnings,
            "rollout_mode": campaign.rollout_mode.value,
            "readiness_report_id": readiness.id if readiness else None,
            "slo_assessment_id": slo_gate.assessment.id if slo_gate.assessment else None,
            "capacity_assessment_id": (
                capacity.assessment.id if capacity.assessment is not None else None
            ),
        },
        request=request,
    )
    db.commit()
    return _campaign_read(_load_campaign(db, campaign.id, user.organization_id))


@router.post("/{campaign_id}/pause", response_model=CampaignRead)
def pause_campaign(
    campaign_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> CampaignRead:
    """Выполнить операцию pause campaign. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    if campaign.status not in {CampaignStatus.SCHEDULED, CampaignStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="Кампания сейчас не выполняется")
    campaign.status = CampaignStatus.PAUSED
    write_audit(
        db,
        action="campaign.paused",
        actor=user,
        entity_type="campaign",
        entity_id=campaign.id,
        request=request,
    )
    db.commit()
    return _campaign_read(_load_campaign(db, campaign.id, user.organization_id))


@router.post("/{campaign_id}/resume", response_model=CampaignRead)
def resume_campaign(
    campaign_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignRead:
    """Выполнить операцию resume campaign. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    if campaign.status != CampaignStatus.PAUSED:
        raise HTTPException(status_code=409, detail="Кампания не находится на паузе")
    if not approval_is_current(campaign):
        raise HTTPException(status_code=409, detail="Кампанию нужно повторно утвердить")
    _ensure_no_uncertain_deliveries(
        db,
        organization_id=user.organization_id,
        campaign_id=campaign.id,
    )
    organization = db.get(Organization, user.organization_id)
    stage_blocker = (
        campaign_stage_blocker(organization, campaign, settings)
        if organization
        else "Организация кампании не найдена"
    )
    if stage_blocker:
        raise HTTPException(status_code=409, detail=stage_blocker)
    if organization and organization.publishing_paused:
        raise HTTPException(
            status_code=409,
            detail=organization.publishing_pause_reason
            or "Публикации остановлены на уровне организации",
        )
    if organization and organization.maintenance_mode:
        raise HTTPException(
            status_code=409,
            detail=organization.maintenance_reason or "Платформа находится в режиме обслуживания",
        )
    slo_gate = _require_publishing_slo_gate(
        db, organization_id=user.organization_id, settings=settings
    )
    readiness = latest_readiness_report(db, campaign=campaign)
    if settings.pilot_readiness_required and not readiness_is_current(readiness, campaign):
        raise HTTPException(
            status_code=409,
            detail="Перед возобновлением создайте актуальный отчёт готовности",
        )
    pending = (
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.campaign_id == campaign.id,
                DeliveryJob.organization_id == user.organization_id,
                DeliveryJob.status.in_(
                    [JobStatus.HELD, JobStatus.PENDING, JobStatus.RETRY, JobStatus.WAITING_REVIEW]
                ),
            )
        )
        or 0
    )
    if pending:
        capacity = capacity_admission_decision(
            db,
            organization_id=user.organization_id,
            settings=settings,
            incoming_jobs=0,
            incoming_ready_jobs=0,
            incoming_runs=0,
            incoming_connection_id=None,
            incoming_due_span_seconds=0,
            source=CapacityAssessmentSource.ADMISSION,
            user=user,
            persist_assessment=True,
        )
    else:
        total_jobs, ready_jobs, due_span_seconds = campaign_capacity_projection(campaign)
        capacity = capacity_admission_decision(
            db,
            organization_id=user.organization_id,
            settings=settings,
            incoming_jobs=total_jobs,
            incoming_ready_jobs=ready_jobs,
            incoming_runs=1,
            incoming_connection_id=campaign.connection_id,
            incoming_due_span_seconds=due_span_seconds,
            source=CapacityAssessmentSource.ADMISSION,
            user=user,
            persist_assessment=True,
        )
    if not capacity.allowed:
        raise HTTPException(status_code=409, detail=capacity.message)
    if pending:
        campaign.status = CampaignStatus.RUNNING
    else:
        campaign.status = CampaignStatus.SCHEDULED
        campaign.next_run_at = campaign.next_run_at or utcnow()
    write_audit(
        db,
        action="campaign.resumed",
        actor=user,
        entity_type="campaign",
        entity_id=campaign.id,
        details={
            "pending_jobs": pending,
            "slo_assessment_id": slo_gate.assessment.id if slo_gate.assessment else None,
            "capacity_assessment_id": (
                capacity.assessment.id if capacity.assessment is not None else None
            ),
        },
        request=request,
    )
    db.commit()
    return _campaign_read(_load_campaign(db, campaign.id, user.organization_id))


@router.post("/{campaign_id}/cancel", response_model=MessageResponse)
def cancel_campaign(
    campaign_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить cancel campaign. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    if campaign.status in {CampaignStatus.COMPLETED, CampaignStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail="Кампания уже завершена")
    _ensure_no_uncertain_deliveries(
        db,
        organization_id=user.organization_id,
        campaign_id=campaign.id,
    )
    now = utcnow()
    invalidate_campaign_approval(db, campaign, reason="кампания отменена")
    campaign.status = CampaignStatus.CANCELLED
    campaign.next_run_at = None
    db.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.campaign_id == campaign.id,
            DeliveryJob.organization_id == user.organization_id,
            DeliveryJob.status.in_(
                [JobStatus.HELD, JobStatus.PENDING, JobStatus.RETRY, JobStatus.WAITING_REVIEW]
            ),
        )
        .values(status=JobStatus.CANCELLED, finished_at=now, error_code="CAMPAIGN_CANCELLED")
    )
    runs = list(
        db.scalars(
            select(CampaignRun).where(
                CampaignRun.campaign_id == campaign.id,
                CampaignRun.organization_id == user.organization_id,
                CampaignRun.status.in_(
                    [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_CHECKPOINT]
                ),
            )
        ).all()
    )
    for run in runs:
        run.status = RunStatus.CANCELLED
        run.finished_at = now
    write_audit(
        db,
        action="campaign.cancelled",
        actor=user,
        entity_type="campaign",
        entity_id=campaign.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Кампания и ожидающие задания отменены")


@router.get("/{campaign_id}/runs", response_model=list[CampaignRunRead])
def list_runs(
    campaign_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CampaignRun]:
    """Прочитать runs. Значение возвращается без несвязанных изменений состояния."""
    _load_campaign(db, campaign_id, _user.organization_id)
    return list(
        db.scalars(
            select(CampaignRun)
            .where(
                CampaignRun.campaign_id == campaign_id,
                CampaignRun.organization_id == _user.organization_id,
            )
            .order_by(CampaignRun.scheduled_for.desc())
        ).all()
    )


@router.post(
    "/{campaign_id}/runs/{run_id}/continue",
    response_model=CampaignRunRead,
)
def continue_staged_run(
    campaign_id: str,
    run_id: str,
    payload: RunCheckpointRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignRun:
    """Выполнить операцию continue staged run. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    run = _load_run(
        db,
        campaign_id=campaign.id,
        run_id=run_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    if run.rollout_mode != RolloutMode.STAGED:
        raise HTTPException(status_code=409, detail="Запуск не использует пакетный режим")
    if run.status != RunStatus.AWAITING_CHECKPOINT:
        raise HTTPException(status_code=409, detail="Запуск не ожидает checkpoint")
    if run.active_batch >= run.total_batches:
        raise HTTPException(status_code=409, detail="Все пакеты уже раскрыты")
    if not approval_is_current(campaign):
        raise HTTPException(status_code=409, detail="Утверждение кампании устарело")
    organization = db.get(Organization, user.organization_id)
    stage_blocker = (
        campaign_stage_blocker(organization, campaign, settings)
        if organization
        else "Организация кампании не найдена"
    )
    if stage_blocker:
        raise HTTPException(status_code=409, detail=stage_blocker)
    if organization and organization.publishing_paused:
        raise HTTPException(
            status_code=409,
            detail=organization.publishing_pause_reason
            or "Публикации остановлены на уровне организации",
        )
    if organization and organization.maintenance_mode:
        raise HTTPException(
            status_code=409,
            detail=organization.maintenance_reason or "Платформа находится в режиме обслуживания",
        )
    health = batch_health(db, run, run.active_batch)
    if health.waiting_review:
        raise HTTPException(
            status_code=409,
            detail=(
                "В активном пакете есть задания, требующие ручной сверки; "
                "сначала обработайте их в очереди"
            ),
        )
    now = utcnow()
    try:
        released = release_next_batch(
            db,
            run=run,
            now=now,
            approved_by_id=user.id,
            note=payload.note,
            settings=settings,
        )
    except CapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if released <= 0:
        raise HTTPException(
            status_code=409, detail="Следующий пакет не содержит удержанных заданий"
        )
    campaign.status = CampaignStatus.RUNNING
    write_audit(
        db,
        actor=user,
        action="rollout.checkpoint_approved",
        entity_type="campaign_run",
        entity_id=run.id,
        details={
            "campaign_id": campaign.id,
            "released_batch": run.active_batch,
            "released_jobs": released,
            "previous_batch_health": health.__dict__,
            "note": payload.note,
        },
        request=request,
    )
    create_notification(
        db,
        settings=settings,
        organization_id=user.organization_id,
        event_type="rollout.checkpoint_approved",
        title="Следующий пакет публикаций разрешён",
        message=(
            f"{user.display_name} разрешил пакет {run.active_batch} из {run.total_batches} "
            f"для кампании «{campaign.name}»."
        ),
        severity=SafetySeverity.INFO,
        entity_type="campaign_run",
        entity_id=run.id,
        dedup_key=f"rollout-checkpoint-approved:{run.id}:{run.active_batch}",
        details={"released_jobs": released},
    )
    db.commit()
    db.refresh(run)
    return run


@router.post(
    "/{campaign_id}/runs/{run_id}/abort",
    response_model=CampaignRunRead,
)
def abort_staged_run(
    campaign_id: str,
    run_id: str,
    payload: RunAbortRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CampaignRun:
    """Выполнить операцию abort staged run. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    campaign = _load_campaign(db, campaign_id, user.organization_id, for_update=True)
    run = _load_run(
        db,
        campaign_id=campaign.id,
        run_id=run_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    if run.status not in {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.AWAITING_CHECKPOINT,
    }:
        raise HTTPException(status_code=409, detail="Запуск уже завершён")
    _ensure_no_uncertain_deliveries(
        db,
        organization_id=user.organization_id,
        campaign_id=campaign.id,
        run_id=run.id,
    )
    processing = int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == user.organization_id,
                DeliveryJob.run_id == run.id,
                DeliveryJob.status == JobStatus.PROCESSING,
            )
        )
        or 0
    )
    if processing:
        raise HTTPException(
            status_code=409,
            detail="Нельзя отменить запуск, пока worker выполняет текущее задание",
        )
    now = utcnow()
    cancelled = cancel_run_jobs(
        db,
        run=run,
        now=now,
        code="ROLLOUT_ABORTED",
        message=payload.reason,
    )
    invalidate_campaign_approval(db, campaign, reason="пакетный запуск остановлен оператором")
    campaign.status = CampaignStatus.PAUSED
    campaign.next_run_at = None
    write_audit(
        db,
        actor=user,
        action="rollout.aborted",
        entity_type="campaign_run",
        entity_id=run.id,
        severity=SafetySeverity.WARNING,
        details={
            "campaign_id": campaign.id,
            "cancelled_jobs": cancelled,
            "active_batch": run.active_batch,
            "reason": payload.reason,
        },
        request=request,
    )
    create_notification(
        db,
        settings=settings,
        organization_id=user.organization_id,
        event_type="rollout.aborted",
        title="Пакетный запуск остановлен",
        message=f"Кампания «{campaign.name}» остановлена: {payload.reason}",
        severity=SafetySeverity.WARNING,
        entity_type="campaign_run",
        entity_id=run.id,
        dedup_key=f"rollout-aborted:{run.id}",
        details={"cancelled_jobs": cancelled},
    )
    db.commit()
    db.refresh(run)
    return run
