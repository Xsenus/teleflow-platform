from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.enums import ExecutionLeaseStatus
from app.models import ExecutionLease, ExecutionSite, User, utcnow
from app.services import commissioning
from app.services.artifact_signing import generate_signing_key
from app.services.commissioning import (
    _check,
    _fingerprint,
    _latest_backup_age_hours,
    run_commissioning_checks,
)


def test_commissioning_helpers_cover_backup_and_fingerprint_states(settings) -> None:
    """Проверить backup age, canonical fingerprint и стандартную структуру check."""
    assert _check("code", "Название", "passed", "Готово") == {
        "code": "code",
        "title": "Название",
        "status": "passed",
        "message": "Готово",
        "details": {},
    }
    remote = settings.model_copy(update={"storage_backend": "s3"})
    assert _latest_backup_age_hours(remote) is None
    assert _latest_backup_age_hours(settings) is None
    settings.backups_path.mkdir(parents=True)
    assert _latest_backup_age_hours(settings) is None
    (settings.backups_path / "backup.bin").write_bytes(b"backup")
    age = _latest_backup_age_hours(settings)
    assert age is not None and 0 <= age < 1

    checks = [_check("one", "Один", "passed", "ok", details={"value": 1})]
    first = _fingerprint(checks, settings)
    second = _fingerprint(checks, settings)
    changed = _fingerprint(
        [_check("one", "Один", "warning", "changed", details={"value": 1})], settings
    )
    assert len(first) == 64 and first == second and changed != first


def test_commissioning_records_dependency_failures_without_crashing(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверить fail-safe отчёт при ошибках migration, storage, recovery, continuity и SLO."""

    class MismatchingStorage:
        """Имитировать хранилище, меняющее probe и не удаляющее временный объект."""

        backend = "test-broken"

        def put_bytes(self, _key: str, _data: bytes) -> None:
            """Принять probe без сохранения исходного содержимого."""

        def read_bytes(self, _key: str) -> bytes:
            """Вернуть отличающееся содержимое для проверки целостности."""
            return b"changed"

        def delete(self, _key: str) -> None:
            """Сымитировать недоступность cleanup, которая не должна сорвать отчёт."""
            raise OSError("cleanup unavailable")

    class OfflineLocks:
        """Имитировать недоступный Redis lock backend."""

        def ping(self) -> bool:
            """Сообщить об отсутствии связи с backend блокировок."""
            return False

    def fail(*_args: object, **_kwargs: object):
        """Сымитировать отказ внешней проверки commissioning."""
        raise RuntimeError("dependency unavailable")

    monkeypatch.setattr(commissioning, "_migration_state", fail)
    monkeypatch.setattr(commissioning, "evaluate_recovery_compliance", fail)
    monkeypatch.setattr(commissioning, "continuity_compliance", fail)
    monkeypatch.setattr(commissioning, "slo_gate_decision", fail)
    monkeypatch.setattr(commissioning.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        commissioning,
        "verify_audit_chain",
        lambda *_args, **_kwargs: SimpleNamespace(
            valid=False,
            checked_entries=0,
            legacy_entries=0,
            head_sequence=None,
            computed_head_hash=None,
            first_error="test corruption",
        ),
    )
    checked_settings = auth_client.app.state.settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://teleflow:test@db/teleflow",
            "storage_backend": "s3",
            "use_redis_locks": True,
            "execution_fencing_required": False,
        }
    )
    with auth_client.app.state.session_factory() as db:
        actor = db.query(User).filter_by(email="owner@example.com").one()
        real_execute = db.execute

        def fail_connectivity_probe(statement, *args, **kwargs):  # type: ignore[no-untyped-def]
            """Отклонить только SELECT 1, сохранив остальные операции тестовой сессии."""
            if str(statement).strip() == "SELECT 1":
                raise OSError("database probe unavailable")
            return real_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", fail_connectivity_probe)
        report = run_commissioning_checks(
            db,
            organization_id=actor.organization_id,
            created_by=actor,
            settings=checked_settings,
            storage=MismatchingStorage(),  # type: ignore[arg-type]
            lock_manager=OfflineLocks(),  # type: ignore[arg-type]
        )
        db.commit()
        codes = {item["code"]: item for item in report.checks}

    assert report.status.value == "blocked"
    assert codes["database_connectivity"]["status"] == "blocked"
    assert codes["database_migrations"]["status"] == "blocked"
    assert codes["storage_roundtrip"]["status"] == "blocked"
    assert codes["distributed_lock"]["status"] == "blocked"
    assert codes["execution_fencing"]["status"] == "warning"
    assert codes["audit_chain"]["status"] == "blocked"
    assert codes["recovery_assurance"]["status"] == "warning"
    assert codes["continuity_assurance"]["status"] == "warning"
    assert codes["operational_slo"]["status"] == "warning"
    assert codes["postgres_backup_tool"]["details"]["available"] is False


def test_commissioning_distinguishes_execution_lease_states(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверить draining, stale heartbeat, expired holder и исправный execution fence."""
    settings = auth_client.app.state.settings.model_copy(update={"slo_gate_required": True})
    now = utcnow()
    with auth_client.app.state.session_factory() as db:
        actor = db.query(User).filter_by(email="owner@example.com").one()
        organization_id = actor.organization_id
        site = ExecutionSite(
            organization_id=organization_id,
            site_key=settings.execution_site_key,
            display_name="Основная площадка",
            enabled=True,
            last_worker_id="worker-1",
            last_seen_at=now,
            details={},
        )
        lease = ExecutionLease(
            organization_id=organization_id,
            active_site_key=settings.execution_site_key,
            holder_worker_id="worker-1",
            epoch=1,
            status=ExecutionLeaseStatus.DRAINING,
            lease_expires_at=now + timedelta(minutes=5),
            last_renewed_at=now,
        )
        db.add_all([site, lease])
        db.commit()

        def execute_check() -> dict:
            """Выполнить commissioning и вернуть execution_fencing check."""
            report = run_commissioning_checks(
                db,
                organization_id=organization_id,
                created_by=actor,
                settings=settings,
                storage=auth_client.app.state.storage,
                lock_manager=auth_client.app.state.lock_manager,
            )
            db.commit()
            return next(item for item in report.checks if item["code"] == "execution_fencing")

        monkeypatch.setattr(commissioning, "_migration_state", lambda _db: ("head", "head"))
        draining = execute_check()
        assert draining["status"] == "blocked" and "draining" in draining["message"]

        lease.status = ExecutionLeaseStatus.ACTIVE
        site.last_seen_at = now - timedelta(hours=1)
        db.commit()
        monkeypatch.setattr(commissioning, "_migration_state", lambda _db: ("old", "head"))
        stale = execute_check()
        assert stale["status"] == "warning" and "устарел" in stale["message"]

        site.last_seen_at = utcnow()
        lease.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        expired = execute_check()
        assert expired["status"] == "blocked" and "TTL" in expired["message"]

        lease.lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()
        healthy = execute_check()
        assert healthy["status"] == "passed"


def test_commissioning_enforces_required_artifact_signing_key(auth_client: TestClient) -> None:
    """Проверить blocked-статус без ключа и для недоверенного default signer."""
    settings = auth_client.app.state.settings.model_copy(
        update={"artifact_signature_policy": "require_trusted"}
    )
    with auth_client.app.state.session_factory() as db:
        actor = db.query(User).filter_by(email="owner@example.com").one()

        def signing_check() -> dict:
            """Выполнить commissioning и вернуть проверку подписи артефактов."""
            report = run_commissioning_checks(
                db,
                organization_id=actor.organization_id,
                created_by=actor,
                settings=settings,
                storage=auth_client.app.state.storage,
                lock_manager=auth_client.app.state.lock_manager,
            )
            db.commit()
            return next(item for item in report.checks if item["code"] == "artifact_signing")

        missing = signing_check()
        assert missing["status"] == "blocked"
        assert "отсутствует" in missing["message"]

        generate_signing_key(
            db,
            organization_id=actor.organization_id,
            created_by=actor,
            cipher=auth_client.app.state.cipher,
            name="Недоверенный commissioning signer",
            make_default=True,
            trusted_for_import=False,
        )
        db.commit()
        untrusted = signing_check()
        assert untrusted["status"] == "blocked"
        assert "не отмечен доверенным" in untrusted["message"]
