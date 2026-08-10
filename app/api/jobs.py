from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_user, require_roles
from app.enums import (
    CampaignStatus,
    ConnectionStatus,
    DeliveryAttemptStatus,
    DeliveryReviewResolution,
    JobStatus,
    PermissionStatus,
    SafetySeverity,
    UserRole,
)
from app.models import DeliveryAttempt, DeliveryJob, User, utcnow
from app.schemas import DeliveryJobRead, DeliveryReviewRequest, MessageResponse
from app.services.approvals import approval_is_current
from app.services.capacity import (
    capacity_admission_decision,
    capacity_ready_release_decision,
)
from app.services.delivery import DeliveryService
from app.services.execution import invalidate_reconciled_delivery_fence
from app.services.notifications import create_notification

router = APIRouter(prefix="/jobs", tags=["delivery-jobs"])


def _get_job(
    db: Session,
    job_id: str,
    organization_id: str,
    *,
    for_update: bool = False,
) -> DeliveryJob:
    """Реализовать внутренний этап get job step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(DeliveryJob).where(
        DeliveryJob.id == job_id,
        DeliveryJob.organization_id == organization_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    job = db.scalar(stmt)
    if not job:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    return job


@router.get("", response_model=list[DeliveryJobRead])
def list_jobs(
    campaign_id: str | None = None,
    status_filter: JobStatus | None = None,
    limit: int = 200,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DeliveryJob]:
    """Прочитать jobs. Значение возвращается без несвязанных изменений состояния."""
    limit = min(max(limit, 1), 500)
    stmt = (
        select(DeliveryJob)
        .where(DeliveryJob.organization_id == user.organization_id)
        .order_by(DeliveryJob.created_at.desc())
        .limit(limit)
    )
    if campaign_id:
        stmt = stmt.where(DeliveryJob.campaign_id == campaign_id)
    if status_filter:
        stmt = stmt.where(DeliveryJob.status == status_filter)
    return list(db.scalars(stmt).all())


@router.get("/{job_id}", response_model=DeliveryJobRead)
def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeliveryJob:
    """Прочитать job. Значение возвращается без несвязанных изменений состояния."""
    return _get_job(db, job_id, user.organization_id)


@router.post("/{job_id}/retry", response_model=DeliveryJobRead)
def retry_job(
    job_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> DeliveryJob:
    """Выполнить операцию retry job. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    job = _get_job(db, job_id, user.organization_id, for_update=True)
    if job.status not in {JobStatus.FAILED, JobStatus.SKIPPED}:
        raise HTTPException(status_code=409, detail="Это задание нельзя поставить на повтор")
    if job.review_resolution is not None:
        raise HTTPException(
            status_code=409,
            detail="Результат уже зафиксирован ручной сверкой и не может быть повторён",
        )
    if job.connection.status != ConnectionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Telegram-подключение не активно")
    if (
        job.destination.permission_status != PermissionStatus.CONFIRMED
        or not job.destination.enabled
    ):
        raise HTTPException(status_code=409, detail="Назначение отключено или не разрешено")
    if job.campaign.status not in {CampaignStatus.SCHEDULED, CampaignStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="Кампания не активна")
    if not approval_is_current(job.campaign):
        raise HTTPException(status_code=409, detail="Утверждение кампании устарело")
    capacity = capacity_admission_decision(
        db,
        organization_id=user.organization_id,
        settings=settings,
        incoming_jobs=1,
        incoming_ready_jobs=1,
        incoming_runs=0,
        incoming_connection_id=job.connection_id,
        incoming_due_span_seconds=0,
        user=user,
        persist_assessment=True,
    )
    if not capacity.allowed:
        raise HTTPException(status_code=409, detail=capacity.message)
    job.status = JobStatus.RETRY
    job.due_at = utcnow()
    job.error_code = None
    job.error_message = None
    job.finished_at = None
    job.locked_at = None
    job.locked_by = None
    write_audit(
        db,
        organization_id=user.organization_id,
        action="delivery.retry_requested",
        actor=user,
        entity_type="delivery_job",
        entity_id=job.id,
        request=request,
    )
    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/cancel", response_model=MessageResponse)
def cancel_job(
    job_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить cancel job. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    job = _get_job(db, job_id, user.organization_id)
    if job.status not in {
        JobStatus.HELD,
        JobStatus.PENDING,
        JobStatus.RETRY,
        JobStatus.WAITING_REVIEW,
    }:
        raise HTTPException(status_code=409, detail="Задание уже выполняется или завершено")
    if job.status == JobStatus.WAITING_REVIEW and job.error_code in {
        "DELIVERY_RESULT_UNCERTAIN",
        "WORKER_CRASH_DURING_SEND",
    }:
        raise HTTPException(
            status_code=409,
            detail="Для неоднозначной доставки используйте ручную сверку результата",
        )
    now = utcnow()
    job.status = JobStatus.CANCELLED
    job.finished_at = now
    job.error_code = "MANUAL_CANCEL"
    delivery = DeliveryService(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.cipher,
        worker_id="api-cancel",
        storage=request.app.state.storage,
    )
    delivery.finalize_run(db, job.run, now)
    write_audit(
        db,
        organization_id=user.organization_id,
        action="delivery.cancelled",
        actor=user,
        entity_type="delivery_job",
        entity_id=job.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Задание отменено")


@router.post("/{job_id}/resolve", response_model=DeliveryJobRead)
def resolve_uncertain_delivery(
    job_id: str,
    payload: DeliveryReviewRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> DeliveryJob:
    """Разрешить an ambiguous Telegram result without unsafe automatic retry."""

    job = db.scalar(
        select(DeliveryJob)
        .where(
            DeliveryJob.id == job_id,
            DeliveryJob.organization_id == user.organization_id,
        )
        .with_for_update()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    if job.status != JobStatus.WAITING_REVIEW:
        raise HTTPException(status_code=409, detail="Задание не ожидает ручной сверки")
    if job.error_code not in {
        "DELIVERY_RESULT_UNCERTAIN",
        "WORKER_CRASH_DURING_SEND",
    }:
        raise HTTPException(
            status_code=409,
            detail=(
                "Это safety-блокировка другого типа. Устраните её причину и используйте "
                "штатную операцию возобновления, а не подтверждение доставки."
            ),
        )

    now = utcnow()
    attempt = db.scalar(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.job_id == job.id,
            DeliveryAttempt.organization_id == user.organization_id,
            DeliveryAttempt.status.in_(
                [
                    DeliveryAttemptStatus.NETWORK_STARTED,
                    DeliveryAttemptStatus.UNCERTAIN,
                ]
            ),
        )
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
        .with_for_update()
    )
    if payload.resolution == DeliveryReviewResolution.CONFIRMED_NOT_SENT:
        capacity = capacity_ready_release_decision(
            db,
            organization_id=user.organization_id,
            settings=request.app.state.settings,
            incoming_ready_jobs=1,
            now=now,
        )
        if not capacity.allowed:
            raise HTTPException(status_code=409, detail=capacity.message)

    execution_epoch_after_reconciliation = invalidate_reconciled_delivery_fence(
        db,
        job=job,
        attempt=attempt,
        now=now,
    )
    job.review_resolution = payload.resolution
    job.reviewed_at = now
    job.reviewed_by_id = user.id
    job.review_note = payload.note
    job.locked_at = None
    job.locked_by = None

    if payload.resolution == DeliveryReviewResolution.CONFIRMED_SENT:
        job.status = JobStatus.SENT
        job.telegram_message_id = payload.telegram_message_id
        job.finished_at = now
        job.next_retry_at = None
        job.error_code = None
        job.error_message = None
        job.connection.last_delivery_at = now
        job.destination.last_sent_at = now
        cooldown = (
            job.destination.cooldown_minutes_override or job.connection.destination_cooldown_minutes
        )
        job.destination.next_allowed_at = now + timedelta(minutes=cooldown)
        job.destination.consecutive_failures = 0
        job.destination.last_error_code = None
        job.destination.last_error_message = None
        if attempt is not None:
            attempt.status = DeliveryAttemptStatus.SENT
            attempt.telegram_message_id = payload.telegram_message_id
            attempt.finished_at = now
            attempt.error_code = None
            attempt.error_message = None
            attempt.details = {
                **(attempt.details or {}),
                "reconciled_by_id": user.id,
                "reconciliation": payload.resolution.value,
            }
        action = "delivery.review_confirmed_sent"
        severity = SafetySeverity.INFO
    elif payload.resolution == DeliveryReviewResolution.CONFIRMED_NOT_SENT:
        if job.connection.status != ConnectionStatus.ACTIVE:
            raise HTTPException(
                status_code=409,
                detail="Сначала восстановите и активируйте Telegram-подключение",
            )
        if (
            job.destination.permission_status != PermissionStatus.CONFIRMED
            or not job.destination.enabled
        ):
            raise HTTPException(
                status_code=409,
                detail="Назначение отключено или разрешение больше не подтверждено",
            )
        if job.campaign.status not in {CampaignStatus.SCHEDULED, CampaignStatus.RUNNING}:
            raise HTTPException(status_code=409, detail="Кампания не активна")
        if not approval_is_current(job.campaign):
            raise HTTPException(status_code=409, detail="Утверждение кампании устарело")
        job.status = JobStatus.RETRY
        job.due_at = now
        job.next_retry_at = now
        job.finished_at = None
        job.telegram_message_id = None
        job.error_code = None
        job.error_message = None
        if attempt is not None:
            attempt.status = DeliveryAttemptStatus.RECONCILED_NOT_SENT
            attempt.finished_at = now
            attempt.error_code = "MANUAL_CONFIRMED_NOT_SENT"
            attempt.error_message = payload.note
            attempt.details = {
                **(attempt.details or {}),
                "reconciled_by_id": user.id,
                "reconciliation": payload.resolution.value,
            }
        action = "delivery.review_confirmed_not_sent"
        severity = SafetySeverity.WARNING
    else:
        job.status = JobStatus.SKIPPED
        job.finished_at = now
        job.next_retry_at = None
        job.telegram_message_id = None
        job.error_code = "MANUAL_REVIEW_SKIPPED"
        job.error_message = payload.note
        if attempt is not None:
            attempt.status = DeliveryAttemptStatus.RECONCILED_SKIPPED
            attempt.finished_at = now
            attempt.error_code = "MANUAL_REVIEW_SKIPPED"
            attempt.error_message = payload.note
            attempt.details = {
                **(attempt.details or {}),
                "reconciled_by_id": user.id,
                "reconciliation": payload.resolution.value,
            }
        action = "delivery.review_skipped"
        severity = SafetySeverity.WARNING

    delivery = DeliveryService(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.cipher,
        worker_id="api-reconciliation",
        storage=request.app.state.storage,
    )
    delivery.finalize_run(db, job.run, now)
    write_audit(
        db,
        organization_id=user.organization_id,
        action=action,
        actor=user,
        entity_type="delivery_job",
        entity_id=job.id,
        severity=severity,
        details={
            "resolution": payload.resolution.value,
            "campaign_id": job.campaign_id,
            "run_id": job.run_id,
            "destination_id": job.destination_id,
            "telegram_message_id": payload.telegram_message_id,
            "note": payload.note,
            "execution_epoch_after_reconciliation": execution_epoch_after_reconciliation,
        },
        request=request,
    )
    create_notification(
        db,
        settings=request.app.state.settings,
        organization_id=user.organization_id,
        event_type=action,
        title="Ручная сверка доставки завершена",
        message=(
            f"Задание {job.id[:8]} обработано: {payload.resolution.value}. "
            "Результат зафиксирован в журнале аудита."
        ),
        severity=severity,
        entity_type="delivery_job",
        entity_id=job.id,
        dedup_key=f"delivery-review:{job.id}:{payload.resolution.value}",
        details={"run_id": job.run_id, "campaign_id": job.campaign_id},
    )
    db.commit()
    db.refresh(job)
    return job
