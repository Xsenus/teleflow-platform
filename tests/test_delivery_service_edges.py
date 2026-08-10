from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.enums import (
    CampaignStatus,
    ConnectionStatus,
    DeliveryAttemptStatus,
    JobStatus,
    PermissionStatus,
    RolloutMode,
    RunStatus,
)
from app.models import (
    DeliveryAttempt,
    DeliveryJob,
    MediaAsset,
    TelegramConnection,
    WorkerHeartbeat,
)
from app.services.capacity import CapacityError
from app.services.delivery import DeliveryService
from app.services.execution import ExecutionError
from app.services.telegram.errors import (
    TelegramAuthError,
    TelegramInvalidRequest,
    TelegramTransientError,
)
from tests.test_delivery_safety import prepare_job


class Rows:
    """Имитировать SQLAlchemy Result с методом all()."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить подготовленные строки результата."""

        self.values = values

    def all(self) -> list[object]:
        """Вернуть подготовленные агрегаты или ORM-строки."""

        return self.values


class DeliveryDb:
    """Предоставить минимальную управляемую сессию для delivery unit-тестов."""

    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        execute_values: list[object] | None = None,
        heartbeat: object | None = None,
    ) -> None:
        """Сохранить ответы scalar/execute/get и счётчики транзакций."""

        self.scalar_values = list(scalar_values or [])
        self.execute_values = list(execute_values or [])
        self.heartbeat = heartbeat
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.added: list[object] = []

    def __enter__(self) -> DeliveryDb:
        """Открыть фиктивную сессию контекстного менеджера."""

        return self

    def __exit__(self, *_args: object) -> None:
        """Закрыть фиктивную сессию без скрытых побочных эффектов."""

    def scalar(self, _statement):  # type: ignore[no-untyped-def]
        """Вернуть очередной подготовленный scalar-ответ."""

        return self.scalar_values.pop(0) if self.scalar_values else None

    def execute(self, _statement) -> Rows:  # type: ignore[no-untyped-def]
        """Вернуть подготовленные агрегаты статусов jobs."""

        return Rows(self.execute_values)

    def get(self, model, _identity):  # type: ignore[no-untyped-def]
        """Вернуть существующий heartbeat по типу модели."""

        return self.heartbeat if model is WorkerHeartbeat else None

    def add(self, value: object) -> None:
        """Сохранить добавленный heartbeat или delivery-объект."""

        self.added.append(value)

    def flush(self) -> None:
        """Зафиксировать вызов ORM flush."""

        self.flushes += 1

    def commit(self) -> None:
        """Зафиксировать успешное завершение транзакции."""

        self.commits += 1

    def rollback(self) -> None:
        """Зафиксировать откат транзакции."""

        self.rollbacks += 1


def _service(db: DeliveryDb, **settings_overrides) -> DeliveryService:  # type: ignore[no-untyped-def]
    """Создать DeliveryService с безопасными минимальными настройками и storage-double."""

    settings = {
        "worker_poll_seconds": 1,
        "execution_site_key": "primary",
        "execution_fencing_required": False,
        "version": "test",
    }
    settings.update(settings_overrides)
    return DeliveryService(
        lambda: db,  # type: ignore[arg-type]
        SimpleNamespace(**settings),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        worker_id="delivery-edge-worker",
        storage=SimpleNamespace(),  # type: ignore[arg-type]
    )


def _job(**overrides):  # type: ignore[no-untyped-def]
    """Создать минимальный job для обработчиков ошибок доставки."""

    values = {
        "id": "delivery-job-id",
        "organization_id": "organization-id",
        "status": JobStatus.PROCESSING,
        "attempt_count": 1,
        "max_attempts": 3,
        "due_at": None,
        "next_retry_at": None,
        "error_code": None,
        "error_message": None,
        "finished_at": None,
        "locked_at": datetime(2026, 8, 10, tzinfo=UTC),
        "locked_by": "worker",
        "execution_epoch": 7,
        "run": SimpleNamespace(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _attempt(status: DeliveryAttemptStatus = DeliveryAttemptStatus.PREPARED):
    """Создать минимальную delivery attempt для durable marker и fence tests."""

    return SimpleNamespace(
        id="attempt-id",
        status=status,
        finished_at=None,
        error_code=None,
        error_message=None,
        network_started_at=None,
    )


def test_durable_network_marker_rejects_missing_and_unexpected_attempt() -> None:
    """Проверить fail-closed marker до Telegram-вызова при потере или смене attempt."""

    now = datetime(2026, 8, 10, tzinfo=UTC)
    missing_service = _service(DeliveryDb(scalar_values=[None]))
    with pytest.raises(ExecutionError, match="отсутствует"):
        missing_service._mark_attempt_network_started_durable("missing", now)

    sent = _attempt(DeliveryAttemptStatus.SENT)
    unexpected_service = _service(DeliveryDb(scalar_values=[sent]))
    with pytest.raises(ExecutionError, match="неожиданный статус"):
        unexpected_service._mark_attempt_network_started_durable(sent.id, now)


def test_fence_loss_abandons_attempt_and_returns_job_to_queue(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить безопасный retry без Telegram-вызова после потери execution fence."""

    monkeypatch.setattr("app.services.delivery.write_audit", lambda *_args, **_kwargs: None)
    db = DeliveryDb()
    service = _service(db, worker_poll_seconds=2)
    job = _job()
    attempt = _attempt()
    now = datetime(2026, 8, 10, tzinfo=UTC)

    service._handle_fence_lost(db, job, attempt, ExecutionError("fence changed"), now)  # type: ignore[arg-type]

    assert attempt.status == DeliveryAttemptStatus.ABANDONED
    assert attempt.error_code == "EXECUTION_FENCE_LOST"
    assert job.status == JobStatus.RETRY
    assert job.due_at == now + timedelta(seconds=5)
    assert job.locked_at is None and job.locked_by is None
    assert db.commits == 1


def test_auth_error_pauses_connection_and_requires_review(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить остановку connection и ручную проверку job после потери авторизации."""

    monkeypatch.setattr("app.services.delivery.write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.delivery.create_notification", lambda *_args, **_kwargs: None)
    db = DeliveryDb()
    service = _service(db)
    job = _job()
    connection = SimpleNamespace(
        id="connection-id",
        name="Bot",
        status=ConnectionStatus.ACTIVE,
        last_error_code=None,
        last_error_message=None,
    )
    now = datetime(2026, 8, 10, tzinfo=UTC)

    service._handle_auth_error(db, job, connection, TelegramAuthError("token expired"), now)  # type: ignore[arg-type]

    assert connection.status == ConnectionStatus.ERROR
    assert job.status == JobStatus.WAITING_REVIEW
    assert job.finished_at == now
    assert db.commits == 1


@pytest.mark.parametrize(
    ("attempt_count", "expected_status", "expected_delay"),
    [
        (1, JobStatus.RETRY, 60),
        (2, JobStatus.RETRY, 300),
        (3, JobStatus.FAILED, None),
    ],
)
def test_transient_error_uses_bounded_backoff_then_fails(
    monkeypatch,
    attempt_count: int,
    expected_status: JobStatus,
    expected_delay: int | None,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить backoff 60/300 секунд и прекращение retries после max_attempts."""

    db = DeliveryDb()
    service = _service(db)
    monkeypatch.setattr(service, "_finalize_run", lambda *_args, **_kwargs: None)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    job = _job(attempt_count=attempt_count, max_attempts=3)

    service._handle_transient(db, job, TelegramTransientError("temporary"), now)  # type: ignore[arg-type]

    assert job.status == expected_status
    if expected_delay is not None:
        assert job.due_at == now + timedelta(seconds=expected_delay)
        assert job.next_retry_at == job.due_at
    else:
        assert job.finished_at == now
    assert db.commits == 1


def test_fail_job_commit_finalizes_run(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить публичный commit-path окончательного отказа job."""

    db = DeliveryDb()
    service = _service(db)
    finalized: list[object] = []
    monkeypatch.setattr(service, "_finalize_run", lambda *_args: finalized.append(True))
    job = _job()
    now = datetime(2026, 8, 10, tzinfo=UTC)

    service._fail_job(db, job, "FAILED", "x" * 3000, now)  # type: ignore[arg-type]

    assert job.status == JobStatus.FAILED
    assert len(job.error_message) == 2000
    assert finalized == [True]
    assert db.commits == 1


def _run(**overrides):  # type: ignore[no-untyped-def]
    """Создать минимальный CampaignRun для ветвей финализации."""

    values = {
        "id": "run-id",
        "organization_id": "organization-id",
        "campaign_id": "campaign-id",
        "status": RunStatus.RUNNING,
        "rollout_mode": RolloutMode.STANDARD,
        "active_batch": 1,
        "total_batches": 1,
        "failure_threshold_percent": 50,
        "checkpoint_required": False,
        "checkpoint_reason": None,
        "checkpoint_requested_at": None,
        "total_jobs": 2,
        "sent_jobs": 0,
        "failed_jobs": 0,
        "finished_at": None,
        "campaign": SimpleNamespace(
            next_run_at=None,
            status=CampaignStatus.RUNNING,
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_finalize_run_preserves_cancelled_checkpoint_and_nonterminal_states(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить ранние выходы для cancelled, awaiting-checkpoint и незавершённого batch."""

    service = _service(DeliveryDb())
    now = datetime(2026, 8, 10, tzinfo=UTC)

    cancelled = _run(status=RunStatus.CANCELLED)
    service._finalize_run(DeliveryDb(execute_values=[]), cancelled, now)  # type: ignore[arg-type]
    assert cancelled.status == RunStatus.CANCELLED

    awaiting = _run(
        status=RunStatus.AWAITING_CHECKPOINT,
        rollout_mode=RolloutMode.STAGED,
        total_batches=2,
    )
    service._finalize_run(DeliveryDb(execute_values=[]), awaiting, now)  # type: ignore[arg-type]
    assert awaiting.finished_at is None

    monkeypatch.setattr(
        "app.services.delivery.batch_health",
        lambda *_args: SimpleNamespace(nonterminal=1, failure_percent=0.0),
    )
    nonterminal = _run(rollout_mode=RolloutMode.STAGED, total_batches=2)
    service._finalize_run(DeliveryDb(execute_values=[]), nonterminal, now)  # type: ignore[arg-type]
    assert nonterminal.status == RunStatus.RUNNING
    assert nonterminal.finished_at is None


def test_finalize_run_holds_next_batch_when_capacity_changes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить переход в checkpoint, если очередь заполнилась перед следующим batch."""

    monkeypatch.setattr("app.services.delivery.write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.delivery.create_notification", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.delivery.batch_health",
        lambda *_args: SimpleNamespace(nonterminal=0, failure_percent=0.0),
    )
    monkeypatch.setattr(
        "app.services.delivery.release_next_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CapacityError("queue full")),
    )
    run = _run(rollout_mode=RolloutMode.STAGED, total_batches=2)
    now = datetime(2026, 8, 10, tzinfo=UTC)

    _service(DeliveryDb())._finalize_run(DeliveryDb(execute_values=[]), run, now)  # type: ignore[arg-type]

    assert run.status == RunStatus.AWAITING_CHECKPOINT
    assert run.checkpoint_reason == "queue full"
    assert run.checkpoint_requested_at == now


def test_finalize_run_marks_partial_and_completes_campaign() -> None:
    """Проверить PARTIAL run и завершение one-shot campaign после всех terminal jobs."""

    run = _run()
    db = DeliveryDb(execute_values=[(JobStatus.SENT, 1), (JobStatus.FAILED, 1)])
    now = datetime(2026, 8, 10, tzinfo=UTC)

    _service(db)._finalize_run(db, run, now)  # type: ignore[arg-type]

    assert run.status == RunStatus.PARTIAL
    assert run.sent_jobs == 1 and run.failed_jobs == 1
    assert run.finished_at == now
    assert run.campaign.status == CampaignStatus.COMPLETED


def test_heartbeat_updates_existing_worker() -> None:
    """Проверить обновление времени и details существующего heartbeat без дубликата."""

    heartbeat = SimpleNamespace(last_seen_at=None, details={"old": True})
    db = DeliveryDb(heartbeat=heartbeat)

    _service(db).heartbeat({"queue": 3})

    assert heartbeat.last_seen_at is not None
    assert heartbeat.details == {"queue": 3}
    assert db.added == []
    assert db.commits == 1


class ErrorGateway:
    """Завершать send_message заданным контролируемым исключением."""

    def __init__(self, error: Exception) -> None:
        """Сохранить исключение, возвращаемое delivery boundary."""

        self.error = error

    def send_message(self, **_kwargs):  # type: ignore[no-untyped-def]
        """Имитировать ошибку внешнего Telegram-вызова."""

        raise self.error


@pytest.mark.parametrize(
    ("error", "expected_job", "expected_connection"),
    [
        (TelegramAuthError("expired"), JobStatus.WAITING_REVIEW, ConnectionStatus.ERROR),
        (TelegramTransientError("temporary"), JobStatus.RETRY, ConnectionStatus.ACTIVE),
        (TelegramInvalidRequest("bad request"), JobStatus.FAILED, ConnectionStatus.ACTIVE),
        (RuntimeError("unexpected"), JobStatus.RETRY, ConnectionStatus.ACTIVE),
    ],
)
def test_process_next_contains_gateway_error_classes(
    auth_client: TestClient,
    monkeypatch,
    error: Exception,
    expected_job: JobStatus,
    expected_connection: ConnectionStatus,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить auth/transient/permanent/internal Telegram error boundaries end-to-end."""

    connection, now = prepare_job(auth_client)
    monkeypatch.setattr(
        "app.services.delivery.build_gateway", lambda *_args, **_kwargs: ErrorGateway(error)
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id=f"error-{type(error).__name__}",
    )

    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        stored_connection = db.get(TelegramConnection, connection["id"])
        assert job.status == expected_job
        assert stored_connection is not None
        assert stored_connection.status == expected_connection
        attempt = db.query(DeliveryAttempt).one()
        assert attempt.status == DeliveryAttemptStatus.FAILED


def test_process_next_returns_false_without_job_or_execution_lease(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить idle worker и read-only поведение worker без execution lease."""

    idle = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="idle-worker",
    )
    assert idle.process_next() is False

    _connection, now = prepare_job(auth_client)
    monkeypatch.setattr(
        "app.services.delivery.claim_delivery_lease",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=False, epoch=0),
    )
    assert idle.process_next(now=now + timedelta(seconds=1)) is False


def test_process_next_recovers_when_fence_changes_before_gateway(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить durable ABANDONED attempt и retry до создания Telegram gateway."""

    _connection, now = prepare_job(auth_client)
    monkeypatch.setattr(
        "app.services.delivery.lock_delivery_fence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ExecutionError("epoch changed")),
    )
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("gateway must not be created after fence loss")
        ),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="fence-loss-worker",
    )

    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        attempt = db.query(DeliveryAttempt).one()
        assert job.status == JobStatus.RETRY
        assert attempt.status == DeliveryAttemptStatus.ABANDONED
        assert attempt.error_code == "EXECUTION_FENCE_LOST"


def test_process_next_rejects_destination_without_chat_id(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить повторный fail-closed guard chat_id непосредственно перед Telegram-вызовом."""

    _connection, now = prepare_job(auth_client)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job.destination.telegram_chat_id = None
        destination_id = job.destination.id
        db.commit()
    monkeypatch.setattr(
        "app.services.delivery.evaluate_delivery",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True, to_dict=lambda: {"allowed": True}),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="missing-chat-id-worker",
    )

    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        destination = job.destination
        assert destination.id == destination_id
        assert destination.permission_status == PermissionStatus.DENIED
        assert destination.enabled is False
        assert job.status == JobStatus.FAILED


def test_process_next_fails_if_materialized_media_disappears(
    auth_client: TestClient,
    tmp_path: Path,
) -> None:
    """Проверить storage metadata и безопасный отказ при исчезновении файла до send_message."""

    _connection, now = prepare_job(auth_client)
    missing_path = tmp_path / "removed-between-materialize-and-send.jpg"
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        asset = MediaAsset(
            organization_id=job.organization_id,
            original_name="vacancy.jpg",
            stored_name="vacancy-edge.jpg",
            relative_path="media/vacancy-edge.jpg",
            content_type="image/jpeg",
            size_bytes=10,
            sha256="a" * 64,
            storage_backend="local",
            storage_key=None,
            created_by_id=job.campaign.created_by_id,
        )
        db.add(asset)
        db.flush()
        job.media_asset_id = asset.id
        db.commit()

    class MissingStorage:
        """Материализовать путь, который исчез до фактической отправки."""

        def __init__(self) -> None:
            """Подготовить журнал переданных storage metadata."""

            self.calls: list[tuple[str, str]] = []

        @contextmanager
        def materialize(self, key: str, *, suffix: str):
            """Вернуть несуществующий путь после успешного чтения metadata."""

            self.calls.append((key, suffix))
            yield missing_path

    storage = MissingStorage()
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="missing-media-worker",
        storage=storage,  # type: ignore[arg-type]
    )

    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    assert storage.calls == [("media/vacancy-edge.jpg", ".jpg")]
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        assert job.status == JobStatus.FAILED
        assert job.error_code == "TELEGRAM_BAD_REQUEST"
