from __future__ import annotations

import contextlib
import logging
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import write_audit
from app.config import Settings
from app.enums import (
    CampaignStatus,
    ConnectionStatus,
    DeliveryAttemptStatus,
    ExecutionLeaseStatus,
    JobStatus,
    PermissionStatus,
    RolloutMode,
    RunStatus,
    SafetySeverity,
)
from app.models import (
    Campaign,
    CampaignRun,
    DeliveryAttempt,
    DeliveryJob,
    Destination,
    ExecutionLease,
    TelegramConnection,
    WorkerHeartbeat,
    utcnow,
)
from app.observability import DELIVERY_RESULTS
from app.security import aware_utc
from app.services.capacity import CapacityError, capacity_dispatch_decision
from app.services.crypto import SecretCipher
from app.services.execution import (
    ExecutionError,
    claim_delivery_lease,
    lock_delivery_fence,
)
from app.services.notifications import create_notification
from app.services.rollouts import batch_health, release_next_batch
from app.services.safety import evaluate_delivery
from app.services.storage import StorageService
from app.services.telegram.errors import (
    TelegramAntiSpamRestriction,
    TelegramAuthError,
    TelegramDeliveryUncertain,
    TelegramFloodWait,
    TelegramGatewayError,
    TelegramInvalidRequest,
    TelegramSlowModeWait,
    TelegramTransientError,
    TelegramWriteForbidden,
)
from app.services.telegram.factory import build_gateway

logger = logging.getLogger(__name__)


class DeliveryService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        cipher: SecretCipher,
        *,
        worker_id: str,
        storage: StorageService | None = None,
    ):
        """Инициализировать DeliveryService with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.session_factory = session_factory
        self.settings = settings
        self.cipher = cipher
        self.worker_id = worker_id
        self.storage = storage or StorageService(settings)

    def recover_stale_jobs(self, now: datetime | None = None) -> int:
        """Выполнить операцию recover stale jobs класса DeliveryService. Аргументы интерпретируются
        в контексте модуля, результат возвращается вызывающему коду.
        """
        now = aware_utc(now) or utcnow()
        stale_before = now - timedelta(minutes=self.settings.job_lease_minutes)
        with self.session_factory() as db:
            jobs = list(
                db.scalars(
                    select(DeliveryJob)
                    .where(
                        DeliveryJob.status == JobStatus.PROCESSING,
                        DeliveryJob.locked_at < stale_before,
                    )
                    .order_by(DeliveryJob.locked_at)
                    .with_for_update(skip_locked=True)
                ).all()
            )
            for job in jobs:
                attempt = db.scalar(
                    select(DeliveryAttempt)
                    .where(DeliveryAttempt.job_id == job.id)
                    .order_by(DeliveryAttempt.attempt_number.desc())
                    .limit(1)
                )
                if attempt is not None and attempt.status == DeliveryAttemptStatus.NETWORK_STARTED:
                    attempt.status = DeliveryAttemptStatus.UNCERTAIN
                    attempt.finished_at = now
                    attempt.error_code = "WORKER_CRASH_DURING_SEND"
                    attempt.error_message = (
                        "Worker потерял lease после начала Telegram-вызова; "
                        "автоматический retry запрещён"
                    )
                    job.status = JobStatus.WAITING_REVIEW
                    job.finished_at = now
                    job.error_code = "WORKER_CRASH_DURING_SEND"
                    job.error_message = (
                        "Невозможно доказать, принял ли Telegram сообщение до остановки worker"
                    )
                    write_audit(
                        db,
                        organization_id=job.organization_id,
                        action="safety.worker_crash_delivery_uncertain",
                        entity_type="delivery_job",
                        entity_id=job.id,
                        severity=SafetySeverity.CRITICAL,
                        details={
                            "attempt_id": attempt.id,
                            "worker_id": attempt.worker_id,
                            "site_key": attempt.site_key,
                            "fence_epoch": attempt.fence_epoch,
                            "automatic_retry_blocked": True,
                        },
                    )
                    create_notification(
                        db,
                        settings=self.settings,
                        organization_id=job.organization_id,
                        event_type="safety.worker_crash_delivery_uncertain",
                        title="Worker остановился во время Telegram-вызова",
                        message=(
                            f"Задание {job.id[:8]} требует ручной сверки в целевом чате. "
                            "Автоматический повтор заблокирован."
                        ),
                        severity=SafetySeverity.CRITICAL,
                        entity_type="delivery_job",
                        entity_id=job.id,
                        dedup_key=f"worker-crash-delivery:{job.id}",
                        details={"attempt_id": attempt.id},
                    )
                else:
                    if attempt is not None and attempt.status == DeliveryAttemptStatus.PREPARED:
                        attempt.status = DeliveryAttemptStatus.ABANDONED
                        attempt.finished_at = now
                        attempt.error_code = "STALE_LEASE_BEFORE_NETWORK"
                        attempt.error_message = "Worker остановился до начала Telegram-вызова"
                    job.status = JobStatus.RETRY
                    job.due_at = now + timedelta(seconds=30)
                    job.error_code = "STALE_LEASE_RECOVERED"
                    job.error_message = "Worker не начал Telegram-вызов до истечения lease"
                job.locked_at = None
                job.locked_by = None
            db.commit()
            return len(jobs)

    def process_next(self, now: datetime | None = None) -> bool:
        """Выполнить process next класса DeliveryService. Операция координирует ограниченные
        побочные эффекты и возвращает детерминированный результат.
        """
        now = aware_utc(now) or utcnow()
        with self.session_factory() as db:
            job = self._select_due_job(db, now)
            if not job:
                return False
            connection = job.connection
            destination = job.destination
            campaign = job.campaign
            lease_decision = claim_delivery_lease(
                db,
                organization_id=job.organization_id,
                settings=self.settings,
                worker_id=self.worker_id,
                now=now,
            )
            if not lease_decision.allowed:
                # Another active worker/site owns the execution lease. Do not
                # mutate the shared job or increase its attempt counter.
                db.rollback()
                return False
            job.execution_site_key = self.settings.execution_site_key
            job.execution_epoch = lease_decision.epoch
            decision = evaluate_delivery(
                db,
                job=job,
                connection=connection,
                destination=destination,
                campaign=campaign,
                settings=self.settings,
                worker_id=self.worker_id,
                fence_epoch=lease_decision.epoch,
                now=now,
            )
            job.safety_decision = decision.to_dict()
            if not decision.allowed:
                if decision.code == "DUPLICATE_CONTENT":
                    job.status = JobStatus.SKIPPED
                    job.error_code = decision.code
                    job.error_message = decision.reason
                    job.finished_at = now
                    job.locked_at = None
                    job.locked_by = None
                    write_audit(
                        db,
                        organization_id=job.organization_id,
                        action="safety.duplicate_delivery_skipped",
                        entity_type="delivery_job",
                        entity_id=job.id,
                        severity=SafetySeverity.WARNING,
                        details={
                            "campaign_id": job.campaign_id,
                            "destination_id": job.destination_id,
                            "content_fingerprint": job.content_fingerprint,
                        },
                    )
                    self._finalize_run(db, job.run, now)
                elif decision.defer_until and not decision.requires_review:
                    job.status = JobStatus.PENDING
                    job.due_at = decision.defer_until
                else:
                    job.status = JobStatus.WAITING_REVIEW
                    job.error_code = decision.code
                    job.error_message = decision.reason
                    severity = (
                        SafetySeverity.CRITICAL
                        if decision.code
                        in {
                            "ORG_EMERGENCY_STOP",
                            "CAMPAIGN_APPROVAL_STALE",
                            "CONNECTION_REQUIRES_REVIEW",
                        }
                        else SafetySeverity.WARNING
                    )
                    write_audit(
                        db,
                        organization_id=job.organization_id,
                        action="safety.delivery_blocked",
                        entity_type="delivery_job",
                        entity_id=job.id,
                        severity=severity,
                        details={
                            "code": decision.code,
                            "campaign_id": job.campaign_id,
                            "connection_id": job.connection_id,
                            "destination_id": job.destination_id,
                            "requires_review": decision.requires_review,
                        },
                    )
                    create_notification(
                        db,
                        settings=self.settings,
                        organization_id=job.organization_id,
                        event_type="safety.delivery_blocked",
                        title="Доставка остановлена safety-политикой",
                        message=decision.reason or "Доставка заблокирована safety-политикой",
                        severity=severity,
                        entity_type="delivery_job",
                        entity_id=job.id,
                        dedup_key=f"safety-delivery-blocked:{decision.code}:{job.connection_id}",
                        details={
                            "code": decision.code,
                            "campaign_id": job.campaign_id,
                            "destination_id": job.destination_id,
                        },
                    )
                db.commit()
                return True

            job.status = JobStatus.PROCESSING
            job.locked_at = now
            job.locked_by = self.worker_id
            job.started_at = now
            job.attempt_count += 1
            run = job.run
            if run.status == RunStatus.QUEUED:
                run.status = RunStatus.RUNNING
                run.started_at = now
            db.commit()

            # Persist the immutable attempt record before acquiring the network
            # fence. If the process stops before the Telegram call, recovery can
            # prove that the remote side was never contacted.
            attempt = DeliveryAttempt(
                organization_id=job.organization_id,
                job_id=job.id,
                attempt_number=job.attempt_count,
                worker_id=self.worker_id,
                site_key=self.settings.execution_site_key,
                fence_epoch=int(job.execution_epoch or 0),
                status=DeliveryAttemptStatus.PREPARED,
                prepared_at=utcnow(),
                details={
                    "connection_id": job.connection_id,
                    "destination_id": job.destination_id,
                    "campaign_id": job.campaign_id,
                },
            )
            db.add(attempt)
            db.commit()

            # Reserve tenant dispatch capacity through a short transaction.
            # The durable PREPARED row is visible to concurrent workers, so the
            # policy-row lock can admit several bounded reservations without
            # holding a database lock during the external Telegram request.
            capacity_reservation = capacity_dispatch_decision(
                db,
                organization_id=job.organization_id,
                settings=self.settings,
                now=utcnow(),
                reservation_attempt_id=attempt.id,
            )
            if not capacity_reservation.allowed:
                deferred_at = capacity_reservation.defer_until or (
                    utcnow() + timedelta(seconds=self.settings.capacity_retry_delay_seconds)
                )
                attempt.status = DeliveryAttemptStatus.ABANDONED
                attempt.finished_at = utcnow()
                attempt.error_code = capacity_reservation.code
                attempt.error_message = capacity_reservation.message[:2000]
                attempt.details = {
                    **(attempt.details or {}),
                    "telegram_call_started": False,
                    "capacity_reservation_rejected": True,
                }
                job.status = JobStatus.PENDING
                job.due_at = deferred_at
                job.next_retry_at = deferred_at
                job.error_code = capacity_reservation.code
                job.error_message = capacity_reservation.message[:2000]
                job.locked_at = None
                job.locked_by = None
                write_audit(
                    db,
                    organization_id=job.organization_id,
                    action="capacity.dispatch_reservation_deferred",
                    entity_type="delivery_job",
                    entity_id=job.id,
                    severity=SafetySeverity.WARNING,
                    details={
                        "attempt_id": attempt.id,
                        "code": capacity_reservation.code,
                        "defer_until": deferred_at.isoformat(),
                        "telegram_call_started": False,
                    },
                )
                db.commit()
                return True

            try:
                lock_delivery_fence(
                    db,
                    organization_id=job.organization_id,
                    settings=self.settings,
                    worker_id=self.worker_id,
                    fence_epoch=job.execution_epoch,
                    now=utcnow(),
                )
            except ExecutionError as exc:
                self._handle_fence_lost(db, job, attempt, exc, utcnow())
                return True

            try:
                gateway = build_gateway(connection, self.settings, self.cipher)
                media_context: contextlib.AbstractContextManager[Path | None] = (
                    contextlib.nullcontext(None)
                )
                media_type: str | None = None
                if job.media_asset:
                    key = job.media_asset.storage_key or job.media_asset.relative_path
                    suffix = Path(job.media_asset.original_name).suffix
                    media_context = self.storage.materialize(key, suffix=suffix)
                    media_type = job.media_asset.content_type
                if destination.telegram_chat_id is None:
                    raise TelegramWriteForbidden("Назначение не прошло проверку и не имеет chat_id")
                with media_context as media_path:
                    if media_path is not None and not media_path.exists():
                        raise TelegramInvalidRequest("Файл вложения отсутствует в хранилище")
                    network_started_at = utcnow()
                    # Commit the NETWORK_STARTED marker through an independent
                    # transaction while this session keeps the execution row
                    # fenced. If the worker dies after Telegram accepts the
                    # request, stale recovery will see durable evidence and
                    # require manual reconciliation instead of auto-retrying.
                    self._mark_attempt_network_started_durable(attempt.id, network_started_at)
                    attempt.status = DeliveryAttemptStatus.NETWORK_STARTED
                    attempt.network_started_at = network_started_at
                    result = gateway.send_message(
                        chat_id=destination.telegram_chat_id,
                        topic_id=destination.topic_id,
                        body=job.body_snapshot,
                        parse_mode=job.parse_mode,
                        link_preview=job.link_preview,
                        media_path=media_path,
                        media_content_type=media_type,
                    )
            except TelegramSlowModeWait as exc:
                self._mark_attempt_failed(attempt, exc.code, str(exc), utcnow())
                DELIVERY_RESULTS.labels("slow_mode", connection.kind.value).inc()
                self._handle_slow_mode(db, job, exc, utcnow())
            except TelegramFloodWait as exc:
                self._mark_attempt_failed(attempt, exc.code, str(exc), utcnow())
                DELIVERY_RESULTS.labels("flood_wait", connection.kind.value).inc()
                self._handle_flood_wait(db, job, connection, exc, utcnow())
            except TelegramAntiSpamRestriction as exc:
                self._mark_attempt_failed(attempt, exc.code, str(exc), utcnow())
                DELIVERY_RESULTS.labels("anti_spam", connection.kind.value).inc()
                self._handle_anti_spam(db, job, connection, exc, utcnow())
            except TelegramWriteForbidden as exc:
                self._mark_attempt_failed(attempt, exc.code, str(exc), utcnow())
                DELIVERY_RESULTS.labels("write_forbidden", connection.kind.value).inc()
                self._handle_write_forbidden(db, job, destination, exc, utcnow())
            except TelegramAuthError as exc:
                self._mark_attempt_failed(attempt, exc.code, str(exc), utcnow())
                DELIVERY_RESULTS.labels("auth_error", connection.kind.value).inc()
                self._handle_auth_error(db, job, connection, exc, utcnow())
            except TelegramDeliveryUncertain as exc:
                self._mark_attempt_uncertain(attempt, exc.code, str(exc), utcnow())
                DELIVERY_RESULTS.labels("uncertain", connection.kind.value).inc()
                self._handle_uncertain_delivery(db, job, connection, exc, utcnow())
            except TelegramTransientError as exc:
                self._mark_attempt_failed(attempt, exc.code, str(exc), utcnow())
                DELIVERY_RESULTS.labels("transient", connection.kind.value).inc()
                self._handle_transient(db, job, exc, utcnow())
            except TelegramGatewayError as exc:
                self._mark_attempt_failed(attempt, exc.code, str(exc), utcnow())
                DELIVERY_RESULTS.labels("failed", connection.kind.value).inc()
                self._fail_job(db, job, exc.code, str(exc), utcnow())
            except Exception:  # defensive boundary: never leak secrets to logs/API
                self._mark_attempt_failed(
                    attempt, "INTERNAL_ERROR", "Внутренняя ошибка worker", utcnow()
                )
                DELIVERY_RESULTS.labels("internal_error", connection.kind.value).inc()
                logger.exception("Необработанная ошибка доставки job=%s", job.id)
                self._handle_transient(
                    db, job, TelegramTransientError("Внутренняя ошибка worker"), utcnow()
                )
            else:
                finished_at = utcnow()
                DELIVERY_RESULTS.labels("sent", connection.kind.value).inc()
                attempt.status = DeliveryAttemptStatus.SENT
                attempt.telegram_message_id = result.message_id
                attempt.finished_at = finished_at
                attempt.error_code = None
                attempt.error_message = None
                job.status = JobStatus.SENT
                job.telegram_message_id = result.message_id
                job.finished_at = finished_at
                job.error_code = None
                job.error_message = None
                job.locked_at = None
                job.locked_by = None
                connection.last_delivery_at = finished_at
                connection.last_error_code = None
                connection.last_error_message = None
                destination.last_sent_at = finished_at
                cooldown_minutes = (
                    destination.cooldown_minutes_override or connection.destination_cooldown_minutes
                )
                destination.next_allowed_at = finished_at + timedelta(minutes=cooldown_minutes)
                destination.consecutive_failures = 0
                destination.last_error_code = None
                destination.last_error_message = None
                write_audit(
                    db,
                    organization_id=job.organization_id,
                    action="delivery.sent",
                    entity_type="delivery_job",
                    entity_id=job.id,
                    details={
                        "campaign_id": job.campaign_id,
                        "destination_id": destination.id,
                        "telegram_message_id": result.message_id,
                        "attempt_id": attempt.id,
                        "execution_site_key": attempt.site_key,
                        "fence_epoch": attempt.fence_epoch,
                    },
                )
                self._finalize_run(db, job.run, finished_at)
                db.commit()
            return True

    def _mark_attempt_network_started_durable(
        self,
        attempt_id: str,
        started_at: datetime,
    ) -> None:
        """Реализовать внутренний этап mark attempt network started durable step класса
        DeliveryService. Вспомогательная функция сохраняет детерминированность и тестируемость
        процесса.
        """
        with self.session_factory() as attempt_db:
            durable = attempt_db.scalar(
                select(DeliveryAttempt).where(DeliveryAttempt.id == attempt_id).with_for_update()
            )
            if durable is None:
                raise ExecutionError("Delivery attempt отсутствует перед Telegram-вызовом")
            if durable.status != DeliveryAttemptStatus.PREPARED:
                raise ExecutionError(
                    f"Delivery attempt имеет неожиданный статус: {durable.status.value}"
                )
            durable.status = DeliveryAttemptStatus.NETWORK_STARTED
            durable.network_started_at = started_at
            attempt_db.commit()

    @staticmethod
    def _mark_attempt_failed(
        attempt: DeliveryAttempt,
        code: str,
        message: str,
        finished_at: datetime,
    ) -> None:
        """Реализовать внутренний этап mark attempt failed step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        attempt.status = DeliveryAttemptStatus.FAILED
        attempt.error_code = code
        attempt.error_message = message[:2000]
        attempt.finished_at = finished_at

    @staticmethod
    def _mark_attempt_uncertain(
        attempt: DeliveryAttempt,
        code: str,
        message: str,
        finished_at: datetime,
    ) -> None:
        """Реализовать внутренний этап mark attempt uncertain step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        attempt.status = DeliveryAttemptStatus.UNCERTAIN
        attempt.error_code = code
        attempt.error_message = message[:2000]
        attempt.finished_at = finished_at

    def _handle_fence_lost(
        self,
        db: Session,
        job: DeliveryJob,
        attempt: DeliveryAttempt,
        exc: ExecutionError,
        now: datetime,
    ) -> None:
        # No Telegram gateway has been created at this point. Preserve the
        # monotonically increasing attempt number, mark the durable PREPARED
        # record abandoned, and safely return the job to the queue.
        """Реализовать внутренний этап handle fence lost step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        attempt.status = DeliveryAttemptStatus.ABANDONED
        attempt.finished_at = now
        attempt.error_code = "EXECUTION_FENCE_LOST"
        attempt.error_message = str(exc)[:2000]
        job.status = JobStatus.RETRY
        job.due_at = now + timedelta(seconds=max(5, self.settings.worker_poll_seconds))
        job.next_retry_at = job.due_at
        job.error_code = "EXECUTION_FENCE_LOST"
        job.error_message = str(exc)[:2000]
        job.locked_at = None
        job.locked_by = None
        write_audit(
            db,
            organization_id=job.organization_id,
            action="execution.delivery_fence_lost",
            entity_type="delivery_job",
            entity_id=job.id,
            severity=SafetySeverity.WARNING,
            details={
                "worker_id": self.worker_id,
                "site_key": self.settings.execution_site_key,
                "fence_epoch": job.execution_epoch,
                "telegram_call_started": False,
            },
        )
        db.commit()

    def _select_due_job(self, db: Session, now: datetime) -> DeliveryJob | None:
        """Реализовать внутренний этап select due job step класса DeliveryService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        stmt = (
            select(DeliveryJob)
            .join(TelegramConnection, DeliveryJob.connection_id == TelegramConnection.id)
            .join(Campaign, DeliveryJob.campaign_id == Campaign.id)
            .outerjoin(
                ExecutionLease,
                ExecutionLease.organization_id == DeliveryJob.organization_id,
            )
            .where(
                DeliveryJob.status.in_([JobStatus.PENDING, JobStatus.RETRY]),
                DeliveryJob.due_at <= now,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
                Campaign.status.in_([CampaignStatus.SCHEDULED, CampaignStatus.RUNNING]),
            )
            .order_by(DeliveryJob.due_at, DeliveryJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if self.settings.execution_fencing_required:
            stmt = stmt.where(
                or_(
                    ExecutionLease.id.is_(None),
                    (
                        (ExecutionLease.active_site_key == self.settings.execution_site_key)
                        & (ExecutionLease.status == ExecutionLeaseStatus.ACTIVE)
                        & or_(
                            ExecutionLease.holder_worker_id.is_(None),
                            ExecutionLease.holder_worker_id == self.worker_id,
                            ExecutionLease.lease_expires_at.is_(None),
                            ExecutionLease.lease_expires_at <= now,
                        )
                    ),
                )
            )
        return db.scalar(stmt)

    def _handle_slow_mode(
        self, db: Session, job: DeliveryJob, exc: TelegramSlowModeWait, now: datetime
    ) -> None:
        """Реализовать внутренний этап handle slow mode step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        delay = max(int(exc.retry_after or 60), 1)
        job.status = JobStatus.RETRY
        job.due_at = now + timedelta(seconds=delay)
        job.next_retry_at = job.due_at
        job.error_code = exc.code
        job.error_message = str(exc)
        job.locked_at = None
        job.locked_by = None
        # Slow mode is returned by Telegram after a real network attempt.
        # Keep attempt_count monotonic so immutable DeliveryAttempt numbers
        # can never be reused on the next retry.
        write_audit(
            db,
            organization_id=job.organization_id,
            action="delivery.slow_mode_deferred",
            entity_type="delivery_job",
            entity_id=job.id,
            severity=SafetySeverity.WARNING,
            details={"retry_after": delay, "destination_id": job.destination_id},
        )
        db.commit()

    def _handle_flood_wait(
        self,
        db: Session,
        job: DeliveryJob,
        connection: TelegramConnection,
        exc: TelegramFloodWait,
        now: datetime,
    ) -> None:
        """Реализовать внутренний этап handle flood wait step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        delay = max(int(exc.retry_after or 300), 60)
        connection.status = ConnectionStatus.PAUSED
        connection.flood_blocked_until = now + timedelta(seconds=delay)
        connection.last_error_code = exc.code
        connection.last_error_message = str(exc)
        job.status = JobStatus.WAITING_REVIEW
        job.error_code = exc.code
        job.error_message = str(exc)
        job.locked_at = None
        job.locked_by = None
        write_audit(
            db,
            organization_id=job.organization_id,
            action="safety.flood_wait_stop",
            entity_type="telegram_connection",
            entity_id=connection.id,
            severity=SafetySeverity.CRITICAL,
            details={"retry_after": delay, "job_id": job.id},
        )
        create_notification(
            db,
            settings=self.settings,
            organization_id=job.organization_id,
            event_type="safety.flood_wait_stop",
            title="Telegram потребовал длительную паузу",
            message=(
                f"Подключение «{connection.name}» остановлено минимум на {delay} секунд. "
                "Перед возобновлением проверьте состояние аккаунта вручную."
            ),
            severity=SafetySeverity.CRITICAL,
            entity_type="telegram_connection",
            entity_id=connection.id,
            dedup_key=f"flood-wait:{connection.id}",
            details={"retry_after": delay, "job_id": job.id},
        )
        db.commit()

    def _handle_anti_spam(
        self,
        db: Session,
        job: DeliveryJob,
        connection: TelegramConnection,
        exc: TelegramAntiSpamRestriction,
        now: datetime,
    ) -> None:
        """Реализовать внутренний этап handle anti spam step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        connection.status = ConnectionStatus.PAUSED
        connection.flood_blocked_until = None
        connection.last_error_code = exc.code
        connection.last_error_message = str(exc)
        job.status = JobStatus.WAITING_REVIEW
        job.error_code = exc.code
        job.error_message = str(exc)
        job.finished_at = now
        job.locked_at = None
        job.locked_by = None
        write_audit(
            db,
            organization_id=job.organization_id,
            action="safety.anti_spam_stop",
            entity_type="telegram_connection",
            entity_id=connection.id,
            severity=SafetySeverity.CRITICAL,
            details={"job_id": job.id, "manual_review_required": True},
        )
        create_notification(
            db,
            settings=self.settings,
            organization_id=job.organization_id,
            event_type="safety.anti_spam_stop",
            title="Обнаружено антиспам-ограничение Telegram",
            message=(
                f"Подключение «{connection.name}» остановлено без автоматических повторов. "
                "Требуется ручная проверка статуса аккаунта."
            ),
            severity=SafetySeverity.CRITICAL,
            entity_type="telegram_connection",
            entity_id=connection.id,
            dedup_key=f"anti-spam:{connection.id}",
            details={"job_id": job.id, "manual_review_required": True},
        )
        db.commit()

    def _handle_write_forbidden(
        self,
        db: Session,
        job: DeliveryJob,
        destination: Destination,
        exc: TelegramWriteForbidden,
        now: datetime,
    ) -> None:
        """Реализовать внутренний этап handle write forbidden step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        destination.enabled = False
        destination.permission_status = PermissionStatus.DENIED
        destination.consecutive_failures += 1
        destination.last_error_code = exc.code
        destination.last_error_message = str(exc)
        self._fail_job(db, job, exc.code, str(exc), now, commit=False)
        write_audit(
            db,
            organization_id=job.organization_id,
            action="safety.destination_disabled",
            entity_type="destination",
            entity_id=destination.id,
            severity=SafetySeverity.WARNING,
            details={"job_id": job.id, "reason": exc.code},
        )
        create_notification(
            db,
            settings=self.settings,
            organization_id=job.organization_id,
            event_type="safety.destination_disabled",
            title="Назначение отключено",
            message=(
                f"Telegram запретил публикацию в «{destination.title}». "
                "Назначение отключено до повторной проверки прав."
            ),
            severity=SafetySeverity.WARNING,
            entity_type="destination",
            entity_id=destination.id,
            dedup_key=f"destination-disabled:{destination.id}",
            details={"job_id": job.id, "reason": exc.code},
        )
        self._finalize_run(db, job.run, now)
        db.commit()

    def _handle_auth_error(
        self,
        db: Session,
        job: DeliveryJob,
        connection: TelegramConnection,
        exc: TelegramAuthError,
        now: datetime,
    ) -> None:
        """Реализовать внутренний этап handle auth error step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        connection.status = ConnectionStatus.ERROR
        connection.last_error_code = exc.code
        connection.last_error_message = str(exc)
        job.status = JobStatus.WAITING_REVIEW
        job.error_code = exc.code
        job.error_message = str(exc)
        job.finished_at = now
        job.locked_at = None
        job.locked_by = None
        write_audit(
            db,
            organization_id=job.organization_id,
            action="safety.connection_auth_failed",
            entity_type="telegram_connection",
            entity_id=connection.id,
            severity=SafetySeverity.CRITICAL,
            details={"job_id": job.id},
        )
        create_notification(
            db,
            settings=self.settings,
            organization_id=job.organization_id,
            event_type="safety.connection_auth_failed",
            title="Telegram-подключение потеряло авторизацию",
            message=(
                f"Подключение «{connection.name}» требует повторной авторизации. "
                "Ожидающие публикации остановлены."
            ),
            severity=SafetySeverity.CRITICAL,
            entity_type="telegram_connection",
            entity_id=connection.id,
            dedup_key=f"connection-auth-failed:{connection.id}",
            details={"job_id": job.id},
        )
        db.commit()

    def _handle_uncertain_delivery(
        self,
        db: Session,
        job: DeliveryJob,
        connection: TelegramConnection,
        exc: TelegramDeliveryUncertain,
        now: datetime,
    ) -> None:
        """Реализовать внутренний этап handle uncertain delivery step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        connection.status = ConnectionStatus.PAUSED
        connection.last_error_code = exc.code
        connection.last_error_message = str(exc)
        job.status = JobStatus.WAITING_REVIEW
        job.error_code = exc.code
        job.error_message = str(exc)
        job.finished_at = now
        job.locked_at = None
        job.locked_by = None
        write_audit(
            db,
            organization_id=job.organization_id,
            action="safety.delivery_result_uncertain",
            entity_type="telegram_connection",
            entity_id=connection.id,
            severity=SafetySeverity.CRITICAL,
            details={
                "job_id": job.id,
                "manual_chat_check_required": True,
                "automatic_retry_blocked": True,
            },
        )
        create_notification(
            db,
            settings=self.settings,
            organization_id=job.organization_id,
            event_type="safety.delivery_result_uncertain",
            title="Результат отправки нельзя определить автоматически",
            message=(
                f"Проверьте целевой чат для задания {job.id[:8]}. "
                "Автоматический повтор заблокирован, чтобы не создать дубликат."
            ),
            severity=SafetySeverity.CRITICAL,
            entity_type="delivery_job",
            entity_id=job.id,
            dedup_key=f"delivery-uncertain:{job.id}",
            details={
                "connection_id": connection.id,
                "manual_chat_check_required": True,
            },
        )
        db.commit()

    def _handle_transient(
        self, db: Session, job: DeliveryJob, exc: TelegramTransientError, now: datetime
    ) -> None:
        """Реализовать внутренний этап handle transient step класса DeliveryService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        if job.attempt_count < job.max_attempts:
            delay = 60 if job.attempt_count == 1 else 300
            job.status = JobStatus.RETRY
            job.due_at = now + timedelta(seconds=delay)
            job.next_retry_at = job.due_at
            job.error_code = exc.code
            job.error_message = str(exc)
            job.locked_at = None
            job.locked_by = None
            db.commit()
            return
        self._fail_job(db, job, exc.code, str(exc), now, commit=False)
        self._finalize_run(db, job.run, now)
        db.commit()

    def _fail_job(
        self,
        db: Session,
        job: DeliveryJob,
        code: str,
        message: str,
        now: datetime,
        *,
        commit: bool = True,
    ) -> None:
        """Реализовать внутренний этап fail job step класса DeliveryService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        job.status = JobStatus.FAILED
        job.error_code = code
        job.error_message = message[:2000]
        job.finished_at = now
        job.locked_at = None
        job.locked_by = None
        if commit:
            self._finalize_run(db, job.run, now)
            db.commit()

    def finalize_run(self, db: Session, run: CampaignRun, now: datetime) -> None:
        """Пересчитать run state after an operator resolves a held result."""

        self._finalize_run(db, run, now)

    def _finalize_run(self, db: Session, run: CampaignRun, now: datetime) -> None:
        """Реализовать внутренний этап finalize run step класса DeliveryService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        db.flush()
        counts: dict[JobStatus, int] = {
            status: int(count)
            for status, count in db.execute(
                select(DeliveryJob.status, func.count(DeliveryJob.id))
                .where(
                    DeliveryJob.run_id == run.id,
                    DeliveryJob.organization_id == run.organization_id,
                )
                .group_by(DeliveryJob.status)
            ).all()
        }
        sent = int(counts.get(JobStatus.SENT, 0))
        failed = int(counts.get(JobStatus.FAILED, 0)) + int(counts.get(JobStatus.SKIPPED, 0))
        run.sent_jobs = sent
        run.failed_jobs = failed

        # An operator may cancel a campaign/run while preserving an ambiguous
        # delivery result for later evidence-based reconciliation. Resolving
        # that job updates counters, but must never resurrect the cancelled run.
        if run.status == RunStatus.CANCELLED:
            return

        # Staged runs expose only one batch at a time. HELD jobs are never
        # selected by the worker and are released only here or by an explicit
        # operator checkpoint endpoint.
        if run.rollout_mode == RolloutMode.STAGED and run.active_batch < run.total_batches:
            if run.status == RunStatus.AWAITING_CHECKPOINT:
                return
            health = batch_health(db, run, run.active_batch)
            if health.nonterminal:
                return

            threshold_exceeded = health.failure_percent > run.failure_threshold_percent
            if run.checkpoint_required or threshold_exceeded:
                reason = (
                    f"Доля неуспешных результатов пакета {health.failure_percent:.1f}% "
                    f"превысила порог {run.failure_threshold_percent}%"
                    if threshold_exceeded
                    else f"Пакет {run.active_batch} завершён; требуется операторский checkpoint"
                )
                run.status = RunStatus.AWAITING_CHECKPOINT
                run.checkpoint_reason = reason
                run.checkpoint_requested_at = now
                write_audit(
                    db,
                    organization_id=run.organization_id,
                    action="rollout.checkpoint_required",
                    entity_type="campaign_run",
                    entity_id=run.id,
                    severity=(
                        SafetySeverity.CRITICAL if threshold_exceeded else SafetySeverity.WARNING
                    ),
                    details={
                        "active_batch": run.active_batch,
                        "total_batches": run.total_batches,
                        "health": health.__dict__,
                        "threshold_exceeded": threshold_exceeded,
                    },
                )
                create_notification(
                    db,
                    settings=self.settings,
                    organization_id=run.organization_id,
                    event_type="rollout.checkpoint_required",
                    title="Пакетный запуск ожидает подтверждения",
                    message=reason,
                    severity=(
                        SafetySeverity.CRITICAL if threshold_exceeded else SafetySeverity.WARNING
                    ),
                    entity_type="campaign_run",
                    entity_id=run.id,
                    dedup_key=f"rollout-checkpoint:{run.id}:{run.active_batch}",
                    details={
                        "campaign_id": run.campaign_id,
                        "active_batch": run.active_batch,
                        "total_batches": run.total_batches,
                        "failure_percent": health.failure_percent,
                    },
                )
                return

            try:
                released = release_next_batch(
                    db,
                    run=run,
                    now=now,
                    settings=self.settings,
                )
            except CapacityError as exc:
                run.status = RunStatus.AWAITING_CHECKPOINT
                run.checkpoint_reason = str(exc)
                run.checkpoint_requested_at = now
                write_audit(
                    db,
                    organization_id=run.organization_id,
                    action="rollout.capacity_checkpoint_required",
                    entity_type="campaign_run",
                    entity_id=run.id,
                    severity=SafetySeverity.WARNING,
                    details={
                        "active_batch": run.active_batch,
                        "next_batch": run.active_batch + 1,
                        "reason": str(exc),
                    },
                )
                create_notification(
                    db,
                    settings=self.settings,
                    organization_id=run.organization_id,
                    event_type="rollout.capacity_checkpoint_required",
                    title="Следующий пакет удержан защитой очереди",
                    message=str(exc),
                    severity=SafetySeverity.WARNING,
                    entity_type="campaign_run",
                    entity_id=run.id,
                    dedup_key=f"rollout-capacity:{run.id}:{run.active_batch + 1}",
                )
                return
            write_audit(
                db,
                organization_id=run.organization_id,
                action="rollout.batch_released",
                entity_type="campaign_run",
                entity_id=run.id,
                details={
                    "batch_number": run.active_batch,
                    "released_jobs": released,
                    "automatic": True,
                },
            )
            return

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
        if nonterminal:
            return
        run.finished_at = now
        if sent == run.total_jobs:
            run.status = RunStatus.COMPLETED
        elif sent > 0:
            run.status = RunStatus.PARTIAL
        else:
            run.status = RunStatus.FAILED
        campaign = run.campaign
        if campaign.next_run_at is None and campaign.status not in {
            CampaignStatus.CANCELLED,
            CampaignStatus.FAILED,
        }:
            campaign.status = CampaignStatus.COMPLETED if sent > 0 else CampaignStatus.FAILED

    def heartbeat(self, details: dict[str, Any] | None = None) -> None:
        """Выполнить операцию heartbeat класса DeliveryService. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        now = utcnow()
        with self.session_factory() as db:
            heartbeat = db.get(WorkerHeartbeat, self.worker_id)
            if not heartbeat:
                heartbeat = WorkerHeartbeat(
                    worker_id=self.worker_id,
                    hostname=socket.gethostname(),
                    pid=__import__("os").getpid(),
                    version=self.settings.version,
                    last_seen_at=now,
                    details=details or {},
                )
                db.add(heartbeat)
            else:
                heartbeat.last_seen_at = now
                heartbeat.details = details or {}
            db.commit()
