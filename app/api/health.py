from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, text

from app.models import WorkerHeartbeat, utcnow
from app.security import aware_utc

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    """Выполнить операцию live. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, object]:
    """Выполнить операцию ready. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    checks: dict[str, object] = {"database": False, "storage": False, "redis": True}
    settings = request.app.state.settings
    try:
        with request.app.state.session_factory() as db:
            db.execute(text("SELECT 1"))
            checks["database"] = True
            heartbeat = db.scalar(
                select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)
            )
            if settings.require_worker_for_readiness:
                checks["worker"] = bool(
                    heartbeat
                    and (aware_utc(heartbeat.last_seen_at) or utcnow())
                    >= utcnow() - timedelta(seconds=settings.worker_readiness_max_age_seconds)
                )
            elif heartbeat:
                checks["worker_last_seen_at"] = heartbeat.last_seen_at.isoformat()
    except Exception:
        checks["database"] = False
    checks["storage"] = bool(request.app.state.storage.healthcheck())
    if settings.use_redis_locks:
        checks["redis"] = bool(request.app.state.lock_manager.ping())
    required = [checks["database"], checks["storage"], checks["redis"]]
    if settings.require_worker_for_readiness:
        required.append(bool(checks.get("worker")))
    if not all(required):
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}
