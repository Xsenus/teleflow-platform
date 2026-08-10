from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import time

from app.config import get_settings
from app.database import create_database_engine, create_session_factory, initialize_database
from app.observability import WORKER_HEARTBEAT
from app.services.capacity import evaluate_due_capacity_policies
from app.services.continuity import ContinuitySyncSummary, synchronize_open_drills
from app.services.crypto import SecretCipher
from app.services.delivery import DeliveryService
from app.services.execution import ExecutionCoordinator
from app.services.inbound import InboundService
from app.services.locks import DistributedLockManager
from app.services.logging import configure_logging
from app.services.operations import evaluate_due_slo_policies
from app.services.outbox import OutboxService
from app.services.privacy import PrivacyService, RetentionService
from app.services.scheduler import SchedulerService
from app.services.storage import StorageService

logger = logging.getLogger(__name__)
_stop = False


def _handle_stop(_signum: int, _frame: object) -> None:
    """Реализовать внутренний этап handle stop step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    global _stop
    _stop = True


def run(*, once: bool = False) -> None:
    """Выполнить операцию run. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    if settings.effective_auto_create_schema:
        initialize_database(engine)
    cipher = SecretCipher(settings.master_key)
    storage = StorageService(settings)
    lock_manager = DistributedLockManager(settings)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    scheduler = SchedulerService(session_factory, settings)
    delivery = DeliveryService(
        session_factory, settings, cipher, worker_id=worker_id, storage=storage
    )
    execution = ExecutionCoordinator(
        session_factory,
        settings,
        worker_id=worker_id,
    )
    inbound = InboundService(session_factory, settings, cipher, worker_id=f"{worker_id}:inbound")
    outbox = OutboxService(
        session_factory, settings, cipher, worker_id=f"{worker_id}:outbox", storage=storage
    )
    privacy = PrivacyService(session_factory, settings, cipher, storage)
    retention = RetentionService(session_factory, settings, storage)
    recovered = delivery.recover_stale_jobs()
    recovered_outbox = outbox.recover_stale()
    if recovered or recovered_outbox:
        logger.warning("Recovered stale work delivery=%s outbox=%s", recovered, recovered_outbox)

    last_heartbeat = 0.0
    last_retention = 0.0
    last_slo_evaluation = 0.0
    last_capacity_evaluation = 0.0
    last_continuity_sync = 0.0
    try:
        while not _stop:
            now_monotonic = time.monotonic()
            if now_monotonic - last_heartbeat >= 10:
                heartbeat_details = {
                    "telegram_fake_mode": settings.telegram_fake_mode,
                    "poll_seconds": settings.worker_poll_seconds,
                    "inbound_enabled": settings.inbound_enabled,
                    "execution_site_key": settings.execution_site_key,
                    "execution_fencing_required": settings.execution_fencing_required,
                }
                execution_result = execution.heartbeat(heartbeat_details)
                delivery.heartbeat({**heartbeat_details, "execution": execution_result})
                WORKER_HEARTBEAT.labels(worker_id).set(time.time())
                last_heartbeat = now_monotonic
            if now_monotonic - last_retention >= 3600 or once:
                with lock_manager.acquire("retention", ttl_seconds=1800) as acquired:
                    if acquired:
                        logger.info("Retention result: %s", retention.run())
                        last_retention = now_monotonic

            slo_count = 0
            if (
                now_monotonic - last_slo_evaluation >= settings.slo_auto_evaluate_minutes * 60
                or once
            ):
                with lock_manager.acquire("slo-evaluation", ttl_seconds=300) as acquired:
                    if acquired:
                        try:
                            with session_factory() as db:
                                slo_count = evaluate_due_slo_policies(db, settings=settings)
                                db.commit()
                            last_slo_evaluation = now_monotonic
                        except Exception:
                            logger.exception("Automatic SLO evaluation failed")

            capacity_count = 0
            if (
                now_monotonic - last_capacity_evaluation
                >= settings.capacity_auto_evaluate_minutes * 60
                or once
            ):
                with lock_manager.acquire(
                    "capacity-evaluation",
                    ttl_seconds=300,
                ) as acquired:
                    if acquired:
                        try:
                            with session_factory() as db:
                                capacity_count = evaluate_due_capacity_policies(
                                    db,
                                    settings=settings,
                                )
                                db.commit()
                            last_capacity_evaluation = now_monotonic
                        except Exception:
                            logger.exception("Automatic capacity evaluation failed")

            continuity_summary = ContinuitySyncSummary()
            if (
                now_monotonic - last_continuity_sync >= settings.continuity_sync_interval_seconds
                or once
            ):
                with lock_manager.acquire("continuity-sync", ttl_seconds=60) as acquired:
                    if acquired:
                        try:
                            with session_factory() as db:
                                continuity_summary = synchronize_open_drills(db, settings=settings)
                                db.commit()
                            last_continuity_sync = now_monotonic
                        except Exception:
                            logger.exception("Automatic continuity synchronization failed")

            with lock_manager.acquire("scheduler", ttl_seconds=30) as acquired:
                runs = scheduler.tick() if acquired else 0
            delivery_count = 0
            inbound_count = 0
            outbox_count = 0
            privacy_count = 0
            while delivery_count < 20 and delivery.process_next():
                delivery_count += 1
            while inbound_count < 50 and inbound.process_next():
                inbound_count += 1
            while outbox_count < 50 and outbox.process_next():
                outbox_count += 1
            while privacy_count < 10 and privacy.process_next():
                privacy_count += 1
            if once:
                logger.info(
                    "Worker once: runs=%s delivery=%s inbound=%s outbox=%s privacy=%s "
                    "slo=%s capacity=%s continuity_scanned=%s "
                    "continuity_updated=%s continuity_errors=%s",
                    runs,
                    delivery_count,
                    inbound_count,
                    outbox_count,
                    privacy_count,
                    slo_count,
                    capacity_count,
                    continuity_summary.scanned,
                    continuity_summary.updated,
                    continuity_summary.errors,
                )
                break
            if not any([runs, delivery_count, inbound_count, outbox_count, privacy_count]):
                time.sleep(settings.worker_poll_seconds)
    finally:
        lock_manager.close()
        engine.dispose()


def main() -> None:
    """Запустить the worker command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(description="TeleFlow unified worker")
    parser.add_argument("--once", action="store_true", help="Выполнить один цикл и завершиться")
    args = parser.parse_args()
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    run(once=args.once)


if __name__ == "__main__":
    main()
