from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import JobStatus, RunStatus
from app.models import CampaignRun, DeliveryJob
from app.services.capacity import CapacityError, capacity_ready_release_decision


@dataclass(frozen=True)
class BatchHealth:
    batch_number: int
    total: int
    sent: int
    failed: int
    skipped: int
    cancelled: int
    waiting_review: int
    nonterminal: int
    failure_percent: float


def batch_health(db: Session, run: CampaignRun, batch_number: int) -> BatchHealth:
    """Выполнить операцию batch health. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    counts: dict[JobStatus, int] = {
        status: int(count)
        for status, count in db.execute(
            select(DeliveryJob.status, func.count(DeliveryJob.id))
            .where(
                DeliveryJob.organization_id == run.organization_id,
                DeliveryJob.run_id == run.id,
                DeliveryJob.batch_number == batch_number,
            )
            .group_by(DeliveryJob.status)
        ).all()
    }
    sent = int(counts.get(JobStatus.SENT, 0))
    failed = int(counts.get(JobStatus.FAILED, 0))
    skipped = int(counts.get(JobStatus.SKIPPED, 0))
    cancelled = int(counts.get(JobStatus.CANCELLED, 0))
    waiting_review = int(counts.get(JobStatus.WAITING_REVIEW, 0))
    nonterminal = sum(
        [
            int(counts.get(status, 0))
            for status in [
                JobStatus.HELD,
                JobStatus.PENDING,
                JobStatus.PROCESSING,
                JobStatus.RETRY,
                JobStatus.WAITING_REVIEW,
            ]
        ]
    )
    total = sum([int(value) for value in counts.values()])
    completed_for_rate = sent + failed + skipped + cancelled
    failure_percent = (
        ((failed + skipped + cancelled) / completed_for_rate) * 100.0 if completed_for_rate else 0.0
    )
    return BatchHealth(
        batch_number=batch_number,
        total=total,
        sent=sent,
        failed=failed,
        skipped=skipped,
        cancelled=cancelled,
        waiting_review=waiting_review,
        nonterminal=nonterminal,
        failure_percent=round(failure_percent, 2),
    )


def release_next_batch(
    db: Session,
    *,
    run: CampaignRun,
    now: datetime,
    approved_by_id: str | None = None,
    note: str | None = None,
    settings: Settings | None = None,
) -> int:
    """Выполнить операцию release next batch. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if run.active_batch >= run.total_batches:
        return 0
    next_batch = run.active_batch + 1
    jobs = list(
        db.scalars(
            select(DeliveryJob)
            .where(
                DeliveryJob.organization_id == run.organization_id,
                DeliveryJob.run_id == run.id,
                DeliveryJob.batch_number == next_batch,
                DeliveryJob.status == JobStatus.HELD,
            )
            .order_by(DeliveryJob.due_at, DeliveryJob.created_at, DeliveryJob.id)
            .with_for_update()
        ).all()
    )
    if settings is not None and jobs:
        capacity = capacity_ready_release_decision(
            db,
            organization_id=run.organization_id,
            settings=settings,
            incoming_ready_jobs=len(jobs),
            now=now,
        )
        if not capacity.allowed:
            raise CapacityError(capacity.message)
    base_due = now + timedelta(seconds=max(0, run.rollout_pause_seconds))
    spacing = max(1, run.campaign.spacing_seconds)
    for position, job in enumerate(jobs):
        job.status = JobStatus.PENDING
        job.due_at = base_due + timedelta(seconds=position * spacing)
        job.next_retry_at = None
        job.error_code = None
        job.error_message = None
        job.finished_at = None
        job.locked_at = None
        job.locked_by = None

    run.active_batch = next_batch
    run.status = RunStatus.RUNNING
    run.checkpoint_reason = None
    run.checkpoint_requested_at = None
    run.checkpoint_approved_at = now if approved_by_id else run.checkpoint_approved_at
    run.checkpoint_approved_by_id = approved_by_id
    run.checkpoint_note = note
    return len(jobs)


def cancel_run_jobs(
    db: Session,
    *,
    run: CampaignRun,
    now: datetime,
    code: str,
    message: str,
) -> int:
    """Безопасно выполнить cancel run jobs. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    result = db.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.organization_id == run.organization_id,
            DeliveryJob.run_id == run.id,
            or_(
                DeliveryJob.status.in_([JobStatus.HELD, JobStatus.PENDING, JobStatus.RETRY]),
                and_(
                    DeliveryJob.status == JobStatus.WAITING_REVIEW,
                    or_(
                        DeliveryJob.error_code.is_(None),
                        ~DeliveryJob.error_code.in_(
                            ["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"]
                        ),
                    ),
                ),
            ),
        )
        .values(
            status=JobStatus.CANCELLED,
            finished_at=now,
            error_code=code,
            error_message=message[:2000],
            locked_at=None,
            locked_by=None,
        )
    )
    run.status = RunStatus.CANCELLED
    run.finished_at = now
    return int(getattr(result, "rowcount", 0) or 0)
