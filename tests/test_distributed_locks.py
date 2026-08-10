from __future__ import annotations

from typing import Any

import pytest
import redis

from app.config import Settings
from app.services.locks import DistributedLockManager


class FakeRedis:
    """Имитировать Redis-клиент и сохранять параметры операций блокировки."""

    def __init__(self) -> None:
        """Инициализировать управляемые ответы и журнал вызовов клиента."""

        self.set_result: object = True
        self.ping_result: object = True
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.closed = False

    def set(self, *args: Any, **kwargs: Any) -> object:
        """Записать запрос SET или выбросить подготовленное исключение."""

        self.calls.append(("set", args, kwargs))
        if isinstance(self.set_result, Exception):
            raise self.set_result
        return self.set_result

    def eval(self, *args: Any, **kwargs: Any) -> int:
        """Записать атомарное освобождение блокировки."""

        self.calls.append(("eval", args, kwargs))
        return 1

    def ping(self) -> object:
        """Вернуть управляемый результат проверки доступности Redis."""

        if isinstance(self.ping_result, Exception):
            raise self.ping_result
        return self.ping_result

    def close(self) -> None:
        """Отметить закрытие соединения."""

        self.closed = True


def redis_settings(settings: Settings) -> Settings:
    """Создать настройки с включёнными распределёнными блокировками."""

    return settings.model_copy(
        update={"use_redis_locks": True, "redis_url": "redis://localhost:6379/9"}
    )


def test_disabled_lock_manager_is_noop(settings: Settings) -> None:
    """Проверить безопасный локальный режим без Redis."""

    manager = DistributedLockManager(settings)

    with manager.acquire("scheduler") as acquired:
        assert acquired is True
    assert manager.ping() is True
    manager.close()


def test_redis_lock_acquire_release_and_ttl_floor(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Проверить захват, минимальный TTL и атомарное освобождение Redis-lock."""

    fake = FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_args, **_kwargs: fake)
    manager = DistributedLockManager(redis_settings(settings))

    with manager.acquire("retention", ttl_seconds=0) as acquired:
        assert acquired is True

    set_call, eval_call = fake.calls
    assert set_call[0] == "set"
    assert set_call[1][0] == "teleflow:lock:retention"
    assert set_call[2] == {"nx": True, "ex": 1}
    assert eval_call[0] == "eval"
    assert eval_call[1][2] == "teleflow:lock:retention"
    assert eval_call[1][3] == set_call[1][1]
    assert manager.ping() is True
    manager.close()
    assert fake.closed is True


def test_redis_lock_contention_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Проверить отказ от singleton-работы при конкуренции и сбое Redis."""

    fake = FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_args, **_kwargs: fake)
    manager = DistributedLockManager(redis_settings(settings))

    fake.set_result = False
    with manager.acquire("scheduler") as acquired:
        assert acquired is False
    assert [call[0] for call in fake.calls] == ["set"]

    fake.calls.clear()
    fake.set_result = ConnectionError("redis unavailable")
    fake.ping_result = ConnectionError("redis unavailable")
    with manager.acquire("scheduler") as acquired:
        assert acquired is False
    assert manager.ping() is False
