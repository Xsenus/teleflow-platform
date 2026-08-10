from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_cipher, get_current_user, require_roles
from app.enums import (
    ConnectionStatus,
    PermissionStatus,
    PilotCanaryStatus,
    ReadinessStatus,
    SafetySeverity,
    SupportBundleStatus,
    UserRole,
)
from app.models import (
    Campaign,
    CampaignApprovalRequest,
    CampaignDestination,
    Destination,
    Organization,
    PilotCanaryAttempt,
    PilotReadinessReport,
    PilotStageAssessment,
    PublishingBlackout,
    SupportBundle,
    TelegramConnection,
    User,
    WorkerHeartbeat,
    utcnow,
)
from app.schemas import (
    MessageResponse,
    PilotAttentionItemRead,
    PilotCanaryCreate,
    PilotCanaryRead,
    PilotOverviewRead,
    PilotReadinessRead,
    PilotStageAdvanceRequest,
    PilotStageAssessmentRead,
    PilotStageAssessmentRequest,
    PilotStageLowerRequest,
    PilotStageOverviewRead,
    SupportBundleCreate,
    SupportBundleRead,
)
from app.security import aware_utc
from app.services.blackouts import blackout_active_until
from app.services.crypto import SecretCipher
from app.services.destination_validation import (
    effective_validation_expiry,
    validation_is_fresh,
)
from app.services.pilot_canary import create_canary_attempt
from app.services.pilot_stages import (
    STAGE_LIMITS,
    apply_stage,
    assessment_is_current,
    create_stage_assessment,
    latest_successful_real_canary,
    lower_confirmation,
    next_stage,
    pause_campaigns_above_stage,
    stage_confirmation,
    stage_index,
)
from app.services.readiness import create_pilot_readiness_report
from app.services.support_bundle import (
    create_support_bundle,
    expire_support_bundles,
)

router = APIRouter(prefix="/pilot", tags=["pilot"])


def _campaign(db: Session, campaign_id: str, organization_id: str) -> Campaign:
    """Реализовать внутренний этап campaign step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    campaign = db.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id, Campaign.organization_id == organization_id)
        .options(
            selectinload(Campaign.connection),
            selectinload(Campaign.template),
            selectinload(Campaign.secondary_template),
            selectinload(Campaign.destinations).selectinload(CampaignDestination.destination),
            selectinload(Campaign.approval_requests).selectinload(
                CampaignApprovalRequest.decisions
            ),
        )
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return campaign


def _organization(db: Session, organization_id: str, *, for_update: bool = False) -> Organization:
    """Реализовать внутренний этап organization step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(Organization).where(Organization.id == organization_id)
    if for_update:
        stmt = stmt.with_for_update()
    organization = db.scalar(stmt)
    if organization is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    return organization


@router.get("/stage", response_model=PilotStageOverviewRead)
def pilot_stage_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotStageOverviewRead:
    """Выполнить операцию pilot stage overview. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    organization = _organization(db, user.organization_id)
    latest = db.scalar(
        select(PilotStageAssessment)
        .where(PilotStageAssessment.organization_id == user.organization_id)
        .order_by(PilotStageAssessment.created_at.desc())
        .limit(1)
    )
    canary = latest_successful_real_canary(db, user.organization_id, settings)
    following = next_stage(organization.pilot_stage)
    return PilotStageOverviewRead(
        current_stage=organization.pilot_stage,
        current_limit=STAGE_LIMITS[organization.pilot_stage],
        next_stage=following,
        next_limit=STAGE_LIMITS[following] if following else None,
        enforcement_required=settings.pilot_stage_enforcement_required,
        latest_assessment=latest,
        successful_real_canary_at=canary.completed_at if canary else None,
        stage_limits={stage.value: limit for stage, limit in STAGE_LIMITS.items()},
    )


@router.get("/stage/assessments", response_model=list[PilotStageAssessmentRead])
def list_stage_assessments(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PilotStageAssessment]:
    """Прочитать stage assessments. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(PilotStageAssessment)
            .where(PilotStageAssessment.organization_id == user.organization_id)
            .order_by(PilotStageAssessment.created_at.desc())
            .limit(min(max(limit, 1), 200))
        ).all()
    )


@router.post("/stage/assess", response_model=PilotStageAssessmentRead, status_code=201)
def assess_pilot_stage(
    payload: PilotStageAssessmentRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotStageAssessment:
    """Выполнить операцию assess pilot stage. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    organization = _organization(db, user.organization_id, for_update=True)
    try:
        assessment = create_stage_assessment(
            db,
            organization=organization,
            requested_stage=payload.requested_stage,
            created_by=user,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        action="pilot.stage_assessed",
        actor=user,
        entity_type="pilot_stage_assessment",
        entity_id=assessment.id,
        severity=(
            SafetySeverity.CRITICAL
            if assessment.status == ReadinessStatus.BLOCKED
            else SafetySeverity.WARNING
            if assessment.status == ReadinessStatus.WARNING
            else SafetySeverity.INFO
        ),
        details={
            "current_stage": assessment.current_stage.value,
            "requested_stage": assessment.requested_stage.value,
            "status": assessment.status.value,
            "blockers": assessment.blockers,
            "warnings": assessment.warnings,
        },
        request=request,
    )
    db.commit()
    db.refresh(assessment)
    return assessment


@router.post("/stage/advance", response_model=PilotStageOverviewRead)
def advance_pilot_stage(
    payload: PilotStageAdvanceRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotStageOverviewRead:
    """Обновить pilot stage. Переход применяется только после проверки его предусловий."""
    organization = _organization(db, user.organization_id, for_update=True)
    assessment = db.scalar(
        select(PilotStageAssessment).where(
            PilotStageAssessment.id == payload.assessment_id,
            PilotStageAssessment.organization_id == user.organization_id,
        )
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Оценка этапа не найдена")
    expected_confirmation = stage_confirmation(assessment.requested_stage)
    if payload.confirmation.strip() != expected_confirmation:
        raise HTTPException(
            status_code=422,
            detail=f"Для подтверждения введите: {expected_confirmation}",
        )
    if not assessment_is_current(
        db,
        organization=organization,
        assessment=assessment,
        settings=settings,
    ):
        raise HTTPException(
            status_code=409,
            detail="Оценка устарела, заблокирована или состояние пилота изменилось",
        )
    previous = organization.pilot_stage
    apply_stage(organization, stage=assessment.requested_stage, actor=user, note=payload.note)
    write_audit(
        db,
        action="pilot.stage_advanced",
        actor=user,
        entity_type="organization",
        entity_id=organization.id,
        severity=SafetySeverity.WARNING,
        details={
            "previous_stage": previous.value,
            "new_stage": organization.pilot_stage.value,
            "assessment_id": assessment.id,
            "note": payload.note,
        },
        request=request,
    )
    db.commit()
    return pilot_stage_overview(user=user, db=db, settings=settings)


@router.post("/stage/lower", response_model=PilotStageOverviewRead)
def lower_pilot_stage(
    payload: PilotStageLowerRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotStageOverviewRead:
    """Выполнить операцию lower pilot stage. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    organization = _organization(db, user.organization_id, for_update=True)
    if stage_index(payload.target_stage) >= stage_index(organization.pilot_stage):
        raise HTTPException(status_code=409, detail="Новый этап должен быть ниже текущего")
    expected_confirmation = lower_confirmation(payload.target_stage)
    if payload.confirmation.strip() != expected_confirmation:
        raise HTTPException(
            status_code=422,
            detail=f"Для подтверждения введите: {expected_confirmation}",
        )
    previous = organization.pilot_stage
    apply_stage(organization, stage=payload.target_stage, actor=user, note=payload.reason)
    paused_campaigns, held_jobs = pause_campaigns_above_stage(
        db, organization=organization, settings=settings
    )
    write_audit(
        db,
        action="pilot.stage_lowered",
        actor=user,
        entity_type="organization",
        entity_id=organization.id,
        severity=SafetySeverity.CRITICAL,
        details={
            "previous_stage": previous.value,
            "new_stage": payload.target_stage.value,
            "reason": payload.reason,
            "paused_campaigns": paused_campaigns,
            "held_jobs": held_jobs,
        },
        request=request,
    )
    db.commit()
    return pilot_stage_overview(user=user, db=db, settings=settings)


@router.get("/canaries", response_model=list[PilotCanaryRead])
def list_canary_attempts(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PilotCanaryAttempt]:
    """Прочитать canary attempts. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(PilotCanaryAttempt)
            .where(PilotCanaryAttempt.organization_id == user.organization_id)
            .order_by(PilotCanaryAttempt.created_at.desc())
            .limit(min(max(limit, 1), 200))
        ).all()
    )


@router.post("/canaries", response_model=PilotCanaryRead, status_code=201)
def send_pilot_canary(
    payload: PilotCanaryCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> PilotCanaryAttempt:
    # Serialize the one-shot check per destination and connection. Without
    # row locks two simultaneous administrator requests could both pass the
    # cooldown/minimum-interval checks before either transaction commits.
    """Выполнить операцию send pilot canary. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    destination = db.scalar(
        select(Destination)
        .where(
            Destination.id == payload.destination_id,
            Destination.organization_id == user.organization_id,
        )
        .with_for_update()
    )
    if destination is None:
        raise HTTPException(status_code=404, detail="Назначение не найдено")
    connection = db.scalar(
        select(TelegramConnection)
        .where(
            TelegramConnection.id == destination.connection_id,
            TelegramConnection.organization_id == user.organization_id,
        )
        .with_for_update()
    )
    if connection is None:
        raise HTTPException(status_code=409, detail="Telegram-подключение назначения отсутствует")
    try:
        attempt = create_canary_attempt(
            db,
            destination=destination,
            connection=connection,
            requested_by=user,
            settings=settings,
            cipher=cipher,
            confirmation=payload.confirmation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    severity = (
        SafetySeverity.INFO
        if attempt.status == PilotCanaryStatus.SENT
        else SafetySeverity.CRITICAL
        if attempt.status == PilotCanaryStatus.BLOCKED
        else SafetySeverity.WARNING
    )
    write_audit(
        db,
        action="pilot.canary_completed",
        actor=user,
        entity_type="pilot_canary_attempt",
        entity_id=attempt.id,
        severity=severity,
        details={
            "destination_id": attempt.destination_id,
            "connection_id": attempt.connection_id,
            "status": attempt.status.value,
            "is_fake": attempt.is_fake,
            "error_code": attempt.error_code,
            "telegram_message_id": attempt.telegram_message_id,
        },
        request=request,
    )
    db.commit()
    db.refresh(attempt)
    return attempt


@router.get("/support-bundles", response_model=list[SupportBundleRead])
def list_support_bundles(
    request: Request,
    limit: int = 50,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> list[SupportBundle]:
    """Прочитать support bundles. Значение возвращается без несвязанных изменений состояния."""
    storage = request.app.state.storage
    if expire_support_bundles(db, organization_id=user.organization_id, storage=storage):
        db.commit()
    return list(
        db.scalars(
            select(SupportBundle)
            .where(SupportBundle.organization_id == user.organization_id)
            .order_by(SupportBundle.created_at.desc())
            .limit(min(max(limit, 1), 200))
        ).all()
    )


@router.post("/support-bundles", response_model=SupportBundleRead, status_code=201)
def generate_support_bundle(
    payload: SupportBundleCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> SupportBundle:
    """Выполнить операцию generate support bundle. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    organization = _organization(db, user.organization_id)
    bundle = create_support_bundle(
        db,
        organization=organization,
        created_by=user,
        settings=settings,
        storage=request.app.state.storage,
        cipher=request.app.state.cipher,
    )
    write_audit(
        db,
        action="pilot.support_bundle_created",
        actor=user,
        entity_type="support_bundle",
        entity_id=bundle.id,
        severity=(
            SafetySeverity.INFO
            if bundle.status == SupportBundleStatus.READY
            else SafetySeverity.WARNING
        ),
        details={
            "status": bundle.status.value,
            "sha256": bundle.sha256,
            "size_bytes": bundle.size_bytes,
            "sections": bundle.sections,
            "reason": payload.reason,
            "signature_status": bundle.signature_status.value,
            "signer_fingerprint": bundle.signer_fingerprint,
        },
        request=request,
    )
    db.commit()
    db.refresh(bundle)
    return bundle


@router.get("/support-bundles/{bundle_id}/download")
def download_support_bundle(
    bundle_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Response:
    """Выполнить операцию download support bundle. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    bundle = db.scalar(
        select(SupportBundle).where(
            SupportBundle.id == bundle_id,
            SupportBundle.organization_id == user.organization_id,
        )
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail="Диагностический архив не найден")
    now = utcnow()
    bundle_expires_at = aware_utc(bundle.expires_at)
    if (
        bundle.status != SupportBundleStatus.READY
        or bundle_expires_at is None
        or bundle_expires_at <= now
    ):
        if bundle.status == SupportBundleStatus.READY:
            expire_support_bundles(
                db,
                organization_id=user.organization_id,
                storage=request.app.state.storage,
                now=now,
            )
            db.commit()
        raise HTTPException(status_code=410, detail="Срок хранения архива истёк")
    if not bundle.storage_key:
        raise HTTPException(status_code=404, detail="Файл архива отсутствует")
    try:
        data = request.app.state.storage.read_bytes(bundle.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Файл архива отсутствует") from exc
    if bundle.sha256 and hashlib.sha256(data).hexdigest() != bundle.sha256:
        raise HTTPException(status_code=409, detail="Контрольная сумма архива не совпадает")
    write_audit(
        db,
        action="pilot.support_bundle_downloaded",
        actor=user,
        entity_type="support_bundle",
        entity_id=bundle.id,
        details={
            "sha256": bundle.sha256,
            "size_bytes": bundle.size_bytes,
            "signature_status": bundle.signature_status.value,
            "signer_fingerprint": bundle.signer_fingerprint,
        },
        request=request,
    )
    db.commit()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="teleflow-support-{bundle.id}.zip"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Artifact-Signature-Status": bundle.signature_status.value,
            "X-Artifact-Signer-Fingerprint": bundle.signer_fingerprint or "",
        },
    )


@router.delete("/support-bundles/{bundle_id}", response_model=MessageResponse)
def delete_support_bundle(
    bundle_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete support bundle. Зависимое состояние и видимые в аудите
    последствия обрабатываются согласованно.
    """
    bundle = db.scalar(
        select(SupportBundle).where(
            SupportBundle.id == bundle_id,
            SupportBundle.organization_id == user.organization_id,
        )
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail="Диагностический архив не найден")
    if bundle.storage_key:
        request.app.state.storage.delete(bundle.storage_key)
    bundle.status = SupportBundleStatus.DELETED
    bundle.deleted_at = utcnow()
    write_audit(
        db,
        action="pilot.support_bundle_deleted",
        actor=user,
        entity_type="support_bundle",
        entity_id=bundle.id,
        details={"sha256": bundle.sha256},
        request=request,
    )
    db.commit()
    return MessageResponse(message="Диагностический архив удалён")


@router.get("/readiness", response_model=list[PilotReadinessRead])
def list_readiness_reports(
    campaign_id: str | None = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PilotReadinessReport]:
    """Прочитать readiness reports. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(PilotReadinessReport).where(
        PilotReadinessReport.organization_id == user.organization_id
    )
    if campaign_id:
        stmt = stmt.where(PilotReadinessReport.campaign_id == campaign_id)
    return list(
        db.scalars(
            stmt.order_by(PilotReadinessReport.created_at.desc()).limit(min(max(limit, 1), 200))
        ).all()
    )


@router.post(
    "/readiness/{campaign_id}",
    response_model=PilotReadinessRead,
    status_code=201,
)
def create_readiness_report(
    campaign_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotReadinessReport:
    """Создать readiness report. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    campaign = _campaign(db, campaign_id, user.organization_id)
    report = create_pilot_readiness_report(
        db,
        campaign=campaign,
        created_by=user,
        settings=settings,
        storage=request.app.state.storage,
    )
    write_audit(
        db,
        action="pilot.readiness_completed",
        actor=user,
        entity_type="pilot_readiness_report",
        entity_id=report.id,
        severity=(
            SafetySeverity.CRITICAL
            if report.status.value == "blocked"
            else SafetySeverity.WARNING
            if report.status.value == "warning"
            else SafetySeverity.INFO
        ),
        details={
            "campaign_id": campaign.id,
            "status": report.status.value,
            "blockers": report.blockers,
            "warnings": report.warnings,
            "preflight_report_id": report.preflight_report_id,
        },
        request=request,
    )
    db.commit()
    db.refresh(report)
    return report


@router.get("/readiness/{campaign_id}/latest", response_model=PilotReadinessRead)
def latest_readiness_report_endpoint(
    campaign_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PilotReadinessReport:
    """Выполнить операцию latest readiness report endpoint. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    _campaign(db, campaign_id, user.organization_id)
    report = db.scalar(
        select(PilotReadinessReport)
        .where(
            PilotReadinessReport.organization_id == user.organization_id,
            PilotReadinessReport.campaign_id == campaign_id,
        )
        .order_by(PilotReadinessReport.created_at.desc())
        .limit(1)
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Отчёт готовности ещё не создавался")
    return report


@router.get("/overview", response_model=PilotOverviewRead)
def pilot_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotOverviewRead:
    """Выполнить операцию pilot overview. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    now = utcnow()
    destinations = list(
        db.scalars(
            select(Destination)
            .where(Destination.organization_id == user.organization_id)
            .order_by(Destination.title)
        ).all()
    )
    attention: list[PilotAttentionItemRead] = []
    validation_due = 0
    permissions_expiring = 0
    permissions_expired = 0
    warning_boundary = now + timedelta(days=settings.permission_expiry_warning_days)
    for destination in destinations:
        validation_expiry = effective_validation_expiry(destination, settings)
        if not validation_is_fresh(destination, settings, now=now):
            validation_due += 1
            attention.append(
                PilotAttentionItemRead(
                    destination_id=destination.id,
                    title=destination.title,
                    issue="Требуется повторная проверка доступа Telegram",
                    severity="blocked",
                    due_at=validation_expiry,
                )
            )
        permission_expiry = aware_utc(destination.permission_expires_at)
        if destination.permission_status == PermissionStatus.CONFIRMED and permission_expiry:
            if permission_expiry <= now:
                permissions_expired += 1
                attention.append(
                    PilotAttentionItemRead(
                        destination_id=destination.id,
                        title=destination.title,
                        issue="Срок разрешения на публикацию истёк",
                        severity="blocked",
                        due_at=permission_expiry,
                    )
                )
            elif permission_expiry <= warning_boundary:
                permissions_expiring += 1
                attention.append(
                    PilotAttentionItemRead(
                        destination_id=destination.id,
                        title=destination.title,
                        issue="Срок разрешения скоро истечёт",
                        severity="warning",
                        due_at=permission_expiry,
                    )
                )

    blackouts = list(
        db.scalars(
            select(PublishingBlackout).where(
                PublishingBlackout.organization_id == user.organization_id,
                PublishingBlackout.enabled.is_(True),
            )
        ).all()
    )
    active_blackouts = sum(blackout_active_until(item, now) is not None for item in blackouts)
    heartbeat = db.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)
    )
    heartbeat_seen_at = aware_utc(heartbeat.last_seen_at) if heartbeat else None
    worker_fresh = bool(
        heartbeat
        and heartbeat_seen_at
        and heartbeat_seen_at > now - timedelta(seconds=settings.worker_readiness_max_age_seconds)
    )
    active_connections = int(
        db.scalar(
            select(func.count(TelegramConnection.id)).where(
                TelegramConnection.organization_id == user.organization_id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
            )
        )
        or 0
    )
    attention.sort(key=lambda item: (item.severity != "blocked", item.due_at or now, item.title))
    return PilotOverviewRead(
        active_connections=active_connections,
        destinations_total=len(destinations),
        destinations_validation_due=validation_due,
        permissions_expiring=permissions_expiring,
        permissions_expired=permissions_expired,
        active_blackouts=active_blackouts,
        worker_fresh=worker_fresh,
        attention=attention[:100],
    )
