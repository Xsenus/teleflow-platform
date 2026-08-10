from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.config import get_settings
from app.database import create_database_engine


def _masked_database_url(url: str) -> str:
    """Реализовать внутренний этап masked database url step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    credentials, host = rest.rsplit("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


def run_checks() -> tuple[dict[str, Any], bool]:
    """Выполнить run checks. Операция координирует ограниченные побочные эффекты и возвращает
    детерминированный результат.
    """
    settings = get_settings()
    results: dict[str, Any] = {
        "application": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "database_url": _masked_database_url(settings.database_url),
        "checks": {},
    }
    ok = True

    try:
        settings.validate_runtime_security()
        results["checks"]["runtime_security"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001 - doctor must aggregate diagnostics
        results["checks"]["runtime_security"] = {"ok": False, "error": str(exc)}
        ok = False

    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = MigrationContext.configure(connection).get_current_revision()
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        head = script.get_current_head()
        schema_ok = current == head
        results["checks"]["database"] = {"ok": True}
        results["checks"]["migration"] = {
            "ok": schema_ok,
            "current": current,
            "head": head,
        }
        ok = ok and schema_ok
    except Exception as exc:  # noqa: BLE001
        results["checks"]["database"] = {"ok": False, "error": str(exc)}
        results["checks"]["migration"] = {"ok": False, "error": "database unavailable"}
        ok = False
    finally:
        engine.dispose()

    if settings.storage_backend == "local":
        try:
            settings.storage_path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=settings.storage_path, prefix=".doctor-", delete=False
            ) as handle:
                handle.write(b"teleflow")
                test_path = Path(handle.name)
            test_path.unlink()
            results["checks"]["storage"] = {
                "ok": True,
                "backend": "local",
                "path": str(settings.storage_path.resolve()),
            }
        except Exception as exc:  # noqa: BLE001
            results["checks"]["storage"] = {"ok": False, "error": str(exc)}
            ok = False
    else:
        configured = all([settings.s3_bucket, settings.s3_access_key, settings.s3_secret_key])
        results["checks"]["storage"] = {
            "ok": configured,
            "backend": "s3",
            "bucket": settings.s3_bucket,
            "note": "Network access is checked on first storage operation",
        }
        ok = ok and configured

    if settings.use_redis_locks:
        try:
            from redis import Redis

            client = Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)
            redis_ok = bool(client.ping())
            results["checks"]["redis"] = {"ok": redis_ok}
            ok = ok and redis_ok
        except Exception as exc:  # noqa: BLE001
            results["checks"]["redis"] = {"ok": False, "error": str(exc)}
            ok = False
    else:
        results["checks"]["redis"] = {"ok": True, "enabled": False}

    results["ok"] = ok
    return results, ok


def main() -> None:
    """Запустить the doctor command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(description="TeleFlow configuration and dependency doctor")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()
    results, ok = run_checks()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"{results['application']} {results['version']} ({results['environment']})")
        print(f"Database: {results['database_url']}")
        for name, result in results["checks"].items():
            marker = "OK" if result.get("ok") else "FAIL"
            details = {key: value for key, value in result.items() if key != "ok"}
            print(f"[{marker}] {name}: {json.dumps(details, ensure_ascii=False)}")
        print("READY" if ok else "NOT READY")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
