from __future__ import annotations

import contextlib
import secrets
from collections.abc import Iterator

from app.config import Settings


class DistributedLockError(RuntimeError):
    pass


class DistributedLockManager:
    """Small Redis-backed lock used only for singleton maintenance/scheduler work.

    Delivery, inbound and outbox consumers still rely on database row locking and
    idempotency. When Redis locks are explicitly enabled, Redis failure is fail-closed:
    singleton work is skipped instead of running concurrently.
    """

    def __init__(self, settings: Settings):
        """Инициализировать DistributedLockManager with its explicit dependencies. Сохраняется
        только состояние, необходимое последующим операциям.
        """
        self.enabled = settings.use_redis_locks
        self._client = None
        if self.enabled:
            try:
                import redis
            except ImportError as exc:  # pragma: no cover - deployment dependency guard
                raise DistributedLockError("Для Redis-lock установите пакет redis") from exc
            self._client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                health_check_interval=30,
            )

    @contextlib.contextmanager
    def acquire(self, name: str, *, ttl_seconds: int = 30) -> Iterator[bool]:
        """Выполнить операцию acquire класса DistributedLockManager. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if not self.enabled:
            yield True
            return
        assert self._client is not None
        token = secrets.token_urlsafe(24)
        key = f"teleflow:lock:{name}"
        acquired = False
        try:
            acquired = bool(self._client.set(key, token, nx=True, ex=max(ttl_seconds, 1)))
        except Exception:
            yield False
            return
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    self._client.eval(
                        "if redis.call('get', KEYS[1]) == ARGV[1] then "
                        "return redis.call('del', KEYS[1]) else return 0 end",
                        1,
                        key,
                        token,
                    )
                except Exception:
                    # TTL guarantees eventual release; never delete another owner's lock.
                    pass

    def ping(self) -> bool:
        """Выполнить операцию ping класса DistributedLockManager. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if not self.enabled:
            return True
        assert self._client is not None
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def close(self) -> None:
        """Выполнить операцию close класса DistributedLockManager. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
