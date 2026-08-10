from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit import write_audit
from app.config import Settings
from app.enums import (
    CampaignApprovalStatus,
    CampaignStatus,
    CapacityAssessmentSource,
    JobStatus,
    PreflightStatus,
    RolloutMode,
    RunStatus,
    SafetySeverity,
    ScheduleType,
)
from app.models import (
    Campaign,
    CampaignApprovalRequest,
    CampaignRun,
    DeliveryJob,
    Organization,
    User,
    utcnow,
)
from app.security import aware_utc
from app.services.approvals import approval_is_current, expire_approval_request
from app.services.campaign_variants import select_campaign_template
from app.services.capacity import CapacityError, capacity_admission_decision
from app.services.content_guard import content_fingerprint
from app.services.execution import scheduler_site_allowed
from app.services.notifications import create_notification
from app.services.operations import slo_gate_decision
from app.services.pilot_stages import campaign_stage_blocker
from app.services.preflight import create_preflight_report
from app.services.readiness import latest_readiness_report, readiness_is_current
from app.services.storage import StorageService


def _zone(name: str) -> ZoneInfo:
    """Реализовать внутренний этап zone step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def next_occurrence(campaign: Campaign, current: datetime) -> datetime | None:
    """Выполнить операцию next occurrence. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    current_utc = aware_utc(current) or current.replace(tzinfo=UTC)
    if campaign.schedule_type == ScheduleType.ONCE:
        return None
    zone = _zone(campaign.timezone_name)
    local = current_utc.astimezone(zone)
    local_time = local.timetz().replace(tzinfo=None)

    if campaign.schedule_type == ScheduleType.DAILY:
        next_date = local.date() + timedelta(days=1)
    else:
        allowed = sorted(set(campaign.weekdays or []))
        if not allowed:
            return None
        next_date = None
        for offset in range(1, 8):
            candidate = local.date() + timedelta(days=offset)
            if candidate.weekday() in allowed:
                next_date = candidate
                break
        if next_date is None:
            return None

    assert next_date is not None
    candidate_local = datetime.combine(next_date, local_time, tzinfo=zone)
    candidate_utc = candidate_local.astimezone(UTC)
    end_at = aware_utc(campaign.end_at)
    if end_at and candidate_utc > end_at:
        return None
    return candidate_utc


def campaign_capacity_projection(campaign: Campaign) -> tuple[int, int, int]:
    """Вернуть total jobs, initially ready jobs and due-time span for one run."""

    links = [link for link in campaign.destinations if link.enabled and link.destination.enabled]
    total_jobs = len(links)
    if campaign.rollout_mode == RolloutMode.STAGED:
        ready_jobs = min(max(1, campaign.rollout_batch_size), total_jobs)
    else:
        ready_jobs = total_jobs
    due_span_seconds = max(0, total_jobs - 1) * max(0, campaign.spacing_seconds)
    return total_jobs, ready_jobs, due_span_seconds


class SchedulerService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings):
        """Инициализировать SchedulerService with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.session_factory = session_factory
        self.settings = settings

    def tick(self, now: datetime | None = None, limit: int = 20) -> int:
        """Выполнить операцию tick класса SchedulerService. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        now = aware_utc(now) or utcnow()
        created = 0
        with self.session_factory() as db:
            self._expire_due_approval_requests(db, now=now, limit=max(limit * 5, 100))
            stmt = (
                select(Campaign)
                .join(Organization, Campaign.organization_id == Organization.id)
                .where(
                    Campaign.status.in_([CampaignStatus.SCHEDULED, CampaignStatus.RUNNING]),
                    Campaign.next_run_at.is_not(None),
                    Campaign.next_run_at <= now,
                    Organization.publishing_paused.is_(False),
                    Organization.maintenance_mode.is_(False),
                )
                .order_by(Campaign.next_run_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            campaigns = list(db.scalars(stmt).unique().all())
            for campaign in campaigns:
                organization = db.get(Organization, campaign.organization_id)
                execution_gate = scheduler_site_allowed(
                    db,
                    organization_id=campaign.organization_id,
                    settings=self.settings,
                    now=now,
                )
                if not execution_gate.allowed:
                    # Standby schedulers remain read-only. They must not pause
                    # or otherwise mutate campaigns owned by the active site.
                    continue
                stage_blocker = (
                    campaign_stage_blocker(organization, campaign, self.settings)
                    if organization
                    else "Организация кампании не найдена"
                )
                if stage_blocker:
                    campaign.status = CampaignStatus.PAUSED
                    campaign.next_run_at = None
                    write_audit(
                        db,
                        organization_id=campaign.organization_id,
                        action="campaign.pilot_stage_blocked",
                        entity_type="campaign",
                        entity_id=campaign.id,
                        severity=SafetySeverity.CRITICAL,
                        details={"scheduler_blocked": True, "reason": stage_blocker},
                    )
                    create_notification(
                        db,
                        settings=self.settings,
                        organization_id=campaign.organization_id,
                        event_type="campaign.pilot_stage_blocked",
                        title="Запуск превышает текущий этап пилота",
                        message=f"Кампания «{campaign.name}» поставлена на паузу: {stage_blocker}",
                        severity=SafetySeverity.CRITICAL,
                        entity_type="campaign",
                        entity_id=campaign.id,
                        dedup_key=f"campaign-pilot-stage:{campaign.id}:{organization.pilot_stage.value if organization else 'missing'}",
                    )
                    continue
                slo_gate = slo_gate_decision(
                    db,
                    organization_id=campaign.organization_id,
                    settings=self.settings,
                    gate="publishing",
                    now=now,
                )
                if not slo_gate.allowed:
                    campaign.status = CampaignStatus.PAUSED
                    campaign.next_run_at = None
                    write_audit(
                        db,
                        organization_id=campaign.organization_id,
                        action="campaign.slo_gate_blocked",
                        entity_type="campaign",
                        entity_id=campaign.id,
                        severity=SafetySeverity.CRITICAL,
                        details={
                            "scheduler_blocked": True,
                            "reason": slo_gate.message,
                            "slo_assessment_id": (
                                slo_gate.assessment.id if slo_gate.assessment else None
                            ),
                        },
                    )
                    create_notification(
                        db,
                        settings=self.settings,
                        organization_id=campaign.organization_id,
                        event_type="campaign.slo_gate_blocked",
                        title="Запуск заблокирован операционным SLO",
                        message=f"Кампания «{campaign.name}» поставлена на паузу: {slo_gate.message}",
                        severity=SafetySeverity.CRITICAL,
                        entity_type="campaign",
                        entity_id=campaign.id,
                        dedup_key=f"campaign-slo-gate:{campaign.id}",
                    )
                    continue
                if not approval_is_current(campaign):
                    campaign.status = CampaignStatus.DRAFT
                    campaign.next_run_at = None
                    campaign.approved_at = None
                    campaign.approved_by_id = None
                    campaign.approved_fingerprint = None
                    write_audit(
                        db,
                        organization_id=campaign.organization_id,
                        action="campaign.approval_stale_blocked",
                        entity_type="campaign",
                        entity_id=campaign.id,
                        severity=SafetySeverity.CRITICAL,
                        details={"scheduler_blocked": True},
                    )
                    create_notification(
                        db,
                        settings=self.settings,
                        organization_id=campaign.organization_id,
                        event_type="campaign.approval_stale_blocked",
                        title="Кампания остановлена до повторного утверждения",
                        message=f"Кампания «{campaign.name}» изменилась после утверждения или не имеет актуального fingerprint.",
                        severity=SafetySeverity.CRITICAL,
                        entity_type="campaign",
                        entity_id=campaign.id,
                        dedup_key=f"campaign-approval-stale:{campaign.id}",
                    )
                    continue
                if self.settings.pilot_readiness_required:
                    readiness = latest_readiness_report(db, campaign=campaign)
                    if not readiness_is_current(readiness, campaign, now=now):
                        campaign.status = CampaignStatus.PAUSED
                        campaign.next_run_at = None
                        write_audit(
                            db,
                            organization_id=campaign.organization_id,
                            action="campaign.readiness_stale_blocked",
                            entity_type="campaign",
                            entity_id=campaign.id,
                            severity=SafetySeverity.CRITICAL,
                            details={
                                "scheduler_blocked": True,
                                "readiness_report_id": readiness.id if readiness else None,
                            },
                        )
                        create_notification(
                            db,
                            settings=self.settings,
                            organization_id=campaign.organization_id,
                            event_type="campaign.readiness_stale_blocked",
                            title="Запуск остановлен до новой проверки готовности",
                            message=(
                                f"Для кампании «{campaign.name}» отсутствует актуальный "
                                "отчёт Production Pilot."
                            ),
                            severity=SafetySeverity.CRITICAL,
                            entity_type="campaign",
                            entity_id=campaign.id,
                            dedup_key=f"campaign-readiness-stale:{campaign.id}",
                        )
                        continue
                # Always create a fresh preflight immediately before a scheduled
                # launch. Permission expiry, duplicate history, cooldowns and
                # storage availability are time-sensitive even when the approval
                # fingerprint itself has not changed.
                creator = db.get(User, campaign.created_by_id)
                if creator is None:
                    campaign.status = CampaignStatus.PAUSED
                    campaign.next_run_at = None
                    write_audit(
                        db,
                        organization_id=campaign.organization_id,
                        action="campaign.preflight_creator_missing",
                        entity_type="campaign",
                        entity_id=campaign.id,
                        severity=SafetySeverity.CRITICAL,
                        details={"scheduler_blocked": True},
                    )
                    continue
                report = create_preflight_report(
                    db,
                    campaign=campaign,
                    created_by=creator,
                    settings=self.settings,
                    storage=StorageService(self.settings),
                    now=now,
                )
                report.summary = {**report.summary, "automated": True, "purpose": "scheduler"}
                write_audit(
                    db,
                    organization_id=campaign.organization_id,
                    action="campaign.preflight_completed",
                    entity_type="campaign_preflight_report",
                    entity_id=report.id,
                    severity=(
                        SafetySeverity.CRITICAL
                        if report.status == PreflightStatus.BLOCKED
                        else SafetySeverity.WARNING
                        if report.status == PreflightStatus.WARNING
                        else SafetySeverity.INFO
                    ),
                    details={
                        "campaign_id": campaign.id,
                        "automated": True,
                        "status": report.status.value,
                        "blockers": report.blockers,
                        "warnings": report.warnings,
                    },
                )
                if report.status == PreflightStatus.BLOCKED:
                    campaign.status = CampaignStatus.PAUSED
                    campaign.next_run_at = None
                    create_notification(
                        db,
                        settings=self.settings,
                        organization_id=campaign.organization_id,
                        event_type="campaign.preflight_blocked",
                        title="Автоматический запуск заблокирован проверкой",
                        message=(
                            f"Кампания «{campaign.name}» поставлена на паузу: "
                            + "; ".join(report.blockers[:3])
                        ),
                        severity=SafetySeverity.CRITICAL,
                        entity_type="campaign",
                        entity_id=campaign.id,
                        dedup_key=f"campaign-preflight-blocked:{report.id}",
                        details={"preflight_report_id": report.id},
                    )
                    continue
                total_jobs, ready_jobs, due_span_seconds = campaign_capacity_projection(campaign)
                capacity = capacity_admission_decision(
                    db,
                    organization_id=campaign.organization_id,
                    settings=self.settings,
                    incoming_jobs=total_jobs,
                    incoming_ready_jobs=ready_jobs,
                    incoming_runs=1,
                    incoming_connection_id=campaign.connection_id,
                    incoming_due_span_seconds=due_span_seconds,
                    source=CapacityAssessmentSource.ADMISSION,
                    user=creator,
                    now=now,
                    persist_assessment=True,
                )
                if not capacity.allowed:
                    campaign.status = CampaignStatus.PAUSED
                    campaign.next_run_at = None
                    write_audit(
                        db,
                        organization_id=campaign.organization_id,
                        action="campaign.capacity_admission_blocked",
                        entity_type="campaign",
                        entity_id=campaign.id,
                        severity=SafetySeverity.CRITICAL,
                        details={
                            "scheduler_blocked": True,
                            "reason": capacity.message,
                            "capacity_assessment_id": (
                                capacity.assessment.id if capacity.assessment is not None else None
                            ),
                        },
                    )
                    create_notification(
                        db,
                        settings=self.settings,
                        organization_id=campaign.organization_id,
                        event_type="campaign.capacity_admission_blocked",
                        title="Запуск остановлен защитой очереди",
                        message=(
                            f"Кампания «{campaign.name}» поставлена на паузу: {capacity.message}"
                        ),
                        severity=SafetySeverity.CRITICAL,
                        entity_type="campaign",
                        entity_id=campaign.id,
                        dedup_key=f"campaign-capacity:{campaign.id}",
                    )
                    continue
                scheduled_for = aware_utc(campaign.next_run_at) or now
                try:
                    # A savepoint prevents one duplicate run from rolling back work
                    # already created for other due campaigns in this tick.
                    with db.begin_nested():
                        self._create_run(db, campaign, scheduled_for)
                    created += 1
                except IntegrityError:
                    db.expire_all()
                    reloaded_campaign = db.get(Campaign, campaign.id)
                    if reloaded_campaign:
                        reloaded_campaign.next_run_at = next_occurrence(
                            reloaded_campaign, scheduled_for
                        )
                except CapacityError as exc:
                    campaign.status = CampaignStatus.PAUSED
                    campaign.next_run_at = None
                    write_audit(
                        db,
                        organization_id=campaign.organization_id,
                        action="campaign.capacity_race_blocked",
                        entity_type="campaign",
                        entity_id=campaign.id,
                        severity=SafetySeverity.CRITICAL,
                        details={"reason": str(exc)},
                    )
            db.commit()
        return created

    def _expire_due_approval_requests(self, db: Session, *, now: datetime, limit: int) -> int:
        """Завершить просроченные stale approval requests under the scheduler lock. The API also
        rejects an expired request at decision time, but the worker performs proactive cleanup
        so dashboard counters, campaign state and notifications stay correct even when nobody
        opens the approval UI.
        """

        stmt = (
            select(CampaignApprovalRequest, Campaign)
            .join(Campaign, Campaign.id == CampaignApprovalRequest.campaign_id)
            .where(
                CampaignApprovalRequest.status == CampaignApprovalStatus.PENDING,
                CampaignApprovalRequest.expires_at <= now,
            )
            .order_by(CampaignApprovalRequest.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        expired = 0
        for approval_request, campaign in db.execute(stmt).all():
            if not expire_approval_request(approval_request, campaign):
                continue
            expired += 1
            write_audit(
                db,
                organization_id=approval_request.organization_id,
                action="campaign.approval_expired",
                entity_type="campaign_approval_request",
                entity_id=approval_request.id,
                severity=SafetySeverity.WARNING,
                details={
                    "campaign_id": campaign.id,
                    "expires_at": approval_request.expires_at.isoformat(),
                },
            )
            create_notification(
                db,
                settings=self.settings,
                organization_id=approval_request.organization_id,
                event_type="campaign.approval_expired",
                title="Срок утверждения кампании истёк",
                message=(
                    f"Запрос на утверждение кампании «{campaign.name}» истёк. "
                    "Перед запуском требуется новая проверка."
                ),
                severity=SafetySeverity.WARNING,
                entity_type="campaign",
                entity_id=campaign.id,
                dedup_key=f"campaign-approval-expired:{approval_request.id}",
                details={"approval_request_id": approval_request.id},
            )
        return expired

    def _create_run(self, db: Session, campaign: Campaign, scheduled_for: datetime) -> None:
        """Реализовать внутренний этап create run step класса SchedulerService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        organization = db.get(Organization, campaign.organization_id)
        execution_gate = scheduler_site_allowed(
            db,
            organization_id=campaign.organization_id,
            settings=self.settings,
            now=scheduled_for,
        )
        if not execution_gate.allowed:
            raise ValueError(execution_gate.message)
        stage_blocker = (
            campaign_stage_blocker(organization, campaign, self.settings)
            if organization
            else "Организация кампании не найдена"
        )
        if stage_blocker:
            raise ValueError(stage_blocker)
        slo_gate = slo_gate_decision(
            db,
            organization_id=campaign.organization_id,
            settings=self.settings,
            gate="publishing",
            now=scheduled_for,
        )
        if not slo_gate.allowed:
            raise ValueError(slo_gate.message)
        links = [
            link
            for link in sorted(campaign.destinations, key=lambda item: (item.position, item.id))
            if link.enabled and link.destination.enabled
        ]
        staged = campaign.rollout_mode == RolloutMode.STAGED
        batch_size = max(1, campaign.rollout_batch_size if staged else len(links) or 1)
        total_batches = max(1, math.ceil(len(links) / batch_size))
        capacity = capacity_admission_decision(
            db,
            organization_id=campaign.organization_id,
            settings=self.settings,
            incoming_jobs=len(links),
            incoming_ready_jobs=min(batch_size, len(links)) if staged else len(links),
            incoming_runs=1,
            incoming_connection_id=campaign.connection_id,
            incoming_due_span_seconds=(max(0, len(links) - 1) * max(0, campaign.spacing_seconds)),
            now=scheduled_for,
            persist_assessment=False,
        )
        if not capacity.allowed:
            raise CapacityError(capacity.message)

        run = CampaignRun(
            organization_id=campaign.organization_id,
            campaign_id=campaign.id,
            scheduled_for=scheduled_for,
            status=RunStatus.QUEUED,
            rollout_mode=campaign.rollout_mode,
            batch_size=batch_size,
            total_batches=total_batches,
            active_batch=1,
            rollout_pause_seconds=max(0, campaign.rollout_pause_seconds),
            failure_threshold_percent=campaign.rollout_failure_threshold_percent,
            checkpoint_required=bool(staged and campaign.rollout_require_checkpoint),
        )
        db.add(run)
        db.flush()

        for position, link in enumerate(links):
            selected_template = select_campaign_template(campaign, link.destination_id)
            body = link.custom_body or selected_template.body
            media_sha = (
                selected_template.media_asset.sha256
                if selected_template.media_asset is not None
                else None
            )
            snapshot_fingerprint = content_fingerprint(
                body=body,
                parse_mode=selected_template.parse_mode,
                media_sha256=media_sha,
                link_preview=selected_template.link_preview,
            )
            idempotency_raw = (
                f"{campaign.id}:{run.id}:{link.destination_id}:"
                f"{selected_template.id}:{selected_template.revision}:{snapshot_fingerprint}"
            )
            idempotency_key = hashlib.sha256(idempotency_raw.encode("utf-8")).hexdigest()
            batch_number = (position // batch_size) + 1 if staged else 1
            initial_status = (
                JobStatus.PENDING if not staged or batch_number == 1 else JobStatus.HELD
            )
            db.add(
                DeliveryJob(
                    organization_id=campaign.organization_id,
                    run_id=run.id,
                    campaign_id=campaign.id,
                    connection_id=campaign.connection_id,
                    destination_id=link.destination_id,
                    template_id=selected_template.id,
                    media_asset_id=selected_template.media_asset_id,
                    body_snapshot=body,
                    parse_mode=selected_template.parse_mode,
                    link_preview=selected_template.link_preview,
                    due_at=scheduled_for + timedelta(seconds=position * campaign.spacing_seconds),
                    status=initial_status,
                    idempotency_key=idempotency_key,
                    batch_number=batch_number,
                    content_fingerprint=snapshot_fingerprint,
                )
            )
        run.total_jobs = len(links)
        campaign.last_run_at = scheduled_for
        campaign.next_run_at = next_occurrence(campaign, scheduled_for)
        if campaign.schedule_type == ScheduleType.ONCE:
            campaign.status = CampaignStatus.RUNNING
        elif campaign.next_run_at is None:
            campaign.status = CampaignStatus.COMPLETED
        else:
            campaign.status = CampaignStatus.SCHEDULED

        if not links:
            run.status = RunStatus.FAILED
            run.finished_at = utcnow()
            if campaign.schedule_type == ScheduleType.ONCE:
                campaign.status = CampaignStatus.FAILED
        db.flush()
