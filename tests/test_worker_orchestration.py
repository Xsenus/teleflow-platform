from __future__ import annotations

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from app import worker
from app.config import Settings
from app.services.continuity import ContinuitySyncSummary


class FakeEngine:
    """Имитировать database engine и фиксировать освобождение ресурсов."""

    def __init__(self) -> None:
        """Создать engine в незакрытом состоянии."""

        self.disposed = False

    def dispose(self) -> None:
        """Зафиксировать вызов освобождения пула соединений."""

        self.disposed = True


class FakeSession:
    """Имитировать короткую worker-транзакцию обслуживания."""

    def __init__(self) -> None:
        """Создать транзакцию с нулевым числом commit."""

        self.commits = 0

    def __enter__(self) -> FakeSession:
        """Вернуть текущую тестовую сессию."""

        return self

    def __exit__(self, *_args: object) -> None:
        """Завершить контекст тестовой сессии."""

    def commit(self) -> None:
        """Зафиксировать успешное завершение maintenance-транзакции."""

        self.commits += 1


class FakeSessionFactory:
    """Создавать и сохранять тестовые worker-сессии."""

    def __init__(self) -> None:
        """Инициализировать журнал созданных сессий."""

        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        """Создать новую сессию для отдельной maintenance-операции."""

        session = FakeSession()
        self.sessions.append(session)
        return session


class FakeLockManager:
    """Имитировать распределённые блокировки worker без Redis."""

    def __init__(self, acquired: bool = True) -> None:
        """Сохранить результат захвата и журнал имён блокировок."""

        self.acquired = acquired
        self.names: list[tuple[str, int]] = []
        self.closed = False

    @contextlib.contextmanager
    def acquire(self, name: str, *, ttl_seconds: int = 30) -> Iterator[bool]:
        """Вернуть управляемый результат захвата singleton-блокировки."""

        self.names.append((name, ttl_seconds))
        yield self.acquired

    def close(self) -> None:
        """Зафиксировать закрытие lock manager."""

        self.closed = True


class SequenceProcessor:
    """Возвращать заданную последовательность результатов process_next."""

    def __init__(self, *results: bool) -> None:
        """Сохранить результаты и обнулить счётчик вызовов."""

        self.results = list(results)
        self.calls = 0

    def process_next(self) -> bool:
        """Вернуть следующий результат либо False после исчерпания списка."""

        self.calls += 1
        return self.results.pop(0) if self.results else False


def install_worker_fakes(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, *, acquired: bool = True
) -> dict[str, Any]:
    """Установить детерминированные зависимости worker и вернуть их состояние."""

    engine = FakeEngine()
    sessions = FakeSessionFactory()
    locks = FakeLockManager(acquired)
    scheduler = SimpleNamespace(tick=lambda: 2)
    delivery_processor = SequenceProcessor(True, True, False)
    delivery = SimpleNamespace(
        recover_stale_jobs=lambda: 1,
        heartbeat_calls=[],
        heartbeat=lambda details: delivery.heartbeat_calls.append(details),
        process_next=delivery_processor.process_next,
    )
    execution = SimpleNamespace(heartbeat=lambda details: {"epoch": 7, "details": details})
    inbound = SequenceProcessor(True, False)
    outbox_processor = SequenceProcessor(False)
    outbox = SimpleNamespace(
        recover_stale=lambda: 1,
        process_next=outbox_processor.process_next,
    )
    privacy = SequenceProcessor(True, False)
    retention = SimpleNamespace(run=lambda: {"deleted": 3})
    metric = SimpleNamespace(values=[], set=lambda value: metric.values.append(value))
    heartbeat_metric = SimpleNamespace(labels=lambda _worker_id: metric)

    monkeypatch.setattr(worker, "_stop", False)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(worker, "create_database_engine", lambda _settings: engine)
    monkeypatch.setattr(worker, "create_session_factory", lambda _engine: sessions)
    initialized: list[FakeEngine] = []
    monkeypatch.setattr(worker, "initialize_database", initialized.append)
    monkeypatch.setattr(worker, "SecretCipher", lambda _key: object())
    monkeypatch.setattr(worker, "StorageService", lambda _settings: object())
    monkeypatch.setattr(worker, "DistributedLockManager", lambda _settings: locks)
    monkeypatch.setattr(worker, "SchedulerService", lambda *_args: scheduler)
    monkeypatch.setattr(worker, "DeliveryService", lambda *_args, **_kwargs: delivery)
    monkeypatch.setattr(worker, "ExecutionCoordinator", lambda *_args, **_kwargs: execution)
    monkeypatch.setattr(worker, "InboundService", lambda *_args, **_kwargs: inbound)
    monkeypatch.setattr(worker, "OutboxService", lambda *_args, **_kwargs: outbox)
    monkeypatch.setattr(worker, "PrivacyService", lambda *_args, **_kwargs: privacy)
    monkeypatch.setattr(worker, "RetentionService", lambda *_args, **_kwargs: retention)
    monkeypatch.setattr(worker, "WORKER_HEARTBEAT", heartbeat_metric)
    monkeypatch.setattr(worker.time, "monotonic", lambda: 10_000.0)
    monkeypatch.setattr(worker.time, "time", lambda: 20_000.0)
    return {
        "engine": engine,
        "sessions": sessions,
        "locks": locks,
        "initialized": initialized,
        "delivery": delivery,
        "delivery_processor": delivery_processor,
        "inbound": inbound,
        "outbox_processor": outbox_processor,
        "privacy": privacy,
        "metric": metric,
    }


def test_worker_once_runs_all_orchestration_paths(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Проверить один полный цикл worker, heartbeat, очереди и maintenance-задачи."""

    state = install_worker_fakes(monkeypatch, settings)
    monkeypatch.setattr(worker, "evaluate_due_slo_policies", lambda *_args, **_kwargs: 4)
    monkeypatch.setattr(worker, "evaluate_due_capacity_policies", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(
        worker,
        "synchronize_open_drills",
        lambda *_args, **_kwargs: ContinuitySyncSummary(scanned=6, updated=2, errors=0),
    )

    worker.run(once=True)

    assert state["initialized"] == [state["engine"]]
    assert [name for name, _ttl in state["locks"].names] == [
        "retention",
        "slo-evaluation",
        "capacity-evaluation",
        "continuity-sync",
        "scheduler",
    ]
    assert len(state["sessions"].sessions) == 3
    assert all(session.commits == 1 for session in state["sessions"].sessions)
    assert state["delivery_processor"].calls == 3
    assert state["inbound"].calls == 2
    assert state["outbox_processor"].calls == 1
    assert state["privacy"].calls == 2
    assert state["delivery"].heartbeat_calls[0]["execution"]["epoch"] == 7
    assert state["metric"].values == [20_000.0]
    assert state["locks"].closed is True
    assert state["engine"].disposed is True


def test_worker_once_survives_maintenance_failures(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Проверить изоляцию сбоев SLO, capacity и continuity с гарантированным cleanup."""

    state = install_worker_fakes(monkeypatch, settings)

    def fail(*_args: object, **_kwargs: object) -> int:
        """Имитировать отказ отдельной maintenance-операции."""

        raise RuntimeError("maintenance unavailable")

    monkeypatch.setattr(worker, "evaluate_due_slo_policies", fail)
    monkeypatch.setattr(worker, "evaluate_due_capacity_policies", fail)
    monkeypatch.setattr(worker, "synchronize_open_drills", fail)

    worker.run(once=True)

    assert len(state["sessions"].sessions) == 3
    assert all(session.commits == 0 for session in state["sessions"].sessions)
    assert state["locks"].closed is True
    assert state["engine"].disposed is True


def test_worker_stop_handler_sets_global_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить перевод worker в состояние остановки обработчиком сигнала."""

    monkeypatch.setattr(worker, "_stop", False)
    worker._handle_stop(15, None)
    assert worker._stop is True
