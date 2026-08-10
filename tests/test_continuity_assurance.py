"""Regression tests for continuity simulation, live failback and evidence integrity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.enums import (
    ContinuityDrillEventType,
    ContinuityDrillMode,
    ContinuityDrillStatus,
    ExecutionLeaseStatus,
    FailoverRequestStatus,
)
from app.models import (
    ContinuityDrill,
    ContinuityDrillEvent,
    ContinuityPolicy,
    ExecutionLease,
    ExecutionSite,
    FailoverRequest,
    Organization,
    User,
)
from app.services.continuity import (
    _canonical_sha256,
    _event_payload,
    continuity_compliance,
    get_or_create_policy,
    synchronize_open_drills,
    verify_event_chain,
)
from app.services.execution import upsert_site_heartbeat
from app.services.runtime_evidence import canonical_sha256, critical_config_payload
from tests.conftest import csrf_headers


def _organization_id(client: TestClient) -> str:
    """Вернуть the bootstrap tenant identifier used by the test client."""

    with client.app.state.session_factory() as db:
        return db.query(User).filter(User.email == "owner@example.com").one().organization_id


def _create_admin(owner: TestClient, *, suffix: str) -> TestClient:
    """Создать and authenticate a second administrator for four-eyes transitions."""

    email = f"{suffix}-admin@example.com"
    password = "ContinuityAdmin_123!"
    created = owner.post(
        "/api/v1/users",
        headers=csrf_headers(owner),
        json={
            "email": email,
            "display_name": "Continuity Admin",
            "password": password,
            "role": "admin",
        },
    )
    assert created.status_code == 201, created.text
    client = TestClient(owner.app)
    logged_in = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert logged_in.status_code == 200, logged_in.text
    return client


def _register_runtime_pair(client: TestClient) -> None:
    """Сохранить fresh, identical runtime evidence for primary and standby sites."""

    settings = client.app.state.settings
    now = datetime.now(UTC)
    organization_id = _organization_id(client)
    with client.app.state.session_factory() as db:
        primary = upsert_site_heartbeat(
            db,
            organization_id=organization_id,
            settings=settings.model_copy(
                update={
                    "execution_site_key": "primary",
                    "execution_site_name": "Основная площадка",
                }
            ),
            worker_id="primary-worker",
            details={"role": "primary", "operator_label": "preserve"},
            now=now,
        )
        standby = upsert_site_heartbeat(
            db,
            organization_id=organization_id,
            settings=settings.model_copy(
                update={
                    "execution_site_key": "standby",
                    "execution_site_name": "Резервная площадка",
                }
            ),
            worker_id="standby-worker",
            details={"role": "standby"},
            now=now,
        )
        db.commit()
        assert primary.runtime_fingerprint == standby.runtime_fingerprint


def _set_simulation_policy(client: TestClient) -> None:
    """Разрешить simulation evidence so the simulation sign-off path can be tested."""

    response = client.patch(
        "/api/v1/continuity/policy",
        headers=csrf_headers(client),
        json={
            "enabled": True,
            "require_live_drill": False,
            "max_rto_seconds": 300,
            "evidence_valid_days": 30,
            "require_distinct_signoff": True,
        },
    )
    assert response.status_code == 200, response.text


def _start_simulation(client: TestClient) -> str:
    """Создать and execute a simulation, returning its drill identifier."""

    created = client.post(
        "/api/v1/continuity/drills",
        headers=csrf_headers(client),
        json={"mode": "simulation", "target_site_key": "standby"},
    )
    assert created.status_code == 201, created.text
    drill_id = created.json()["id"]
    started = client.post(
        f"/api/v1/continuity/drills/{drill_id}/start",
        headers=csrf_headers(client),
    )
    assert started.status_code == 200, started.text
    return drill_id


def _complete_live_drill_without_signoff(owner: TestClient, admin: TestClient) -> str:
    """Запустить a complete failover/failback cycle and return an unsigned drill ID."""

    created = owner.post(
        "/api/v1/continuity/drills",
        headers=csrf_headers(owner),
        json={"mode": "live", "target_site_key": "standby"},
    )
    assert created.status_code == 201, created.text
    drill_id = created.json()["id"]
    started = owner.post(
        f"/api/v1/continuity/drills/{drill_id}/start",
        headers=csrf_headers(owner),
    )
    assert started.status_code == 200, started.text
    first_id = started.json()["failover_request_id"]
    approved = admin.post(
        f"/api/v1/execution/failovers/{first_id}/approve",
        headers=csrf_headers(admin),
        json={"confirmation": "ПЕРЕКЛЮЧИТЬ НА standby"},
    )
    assert approved.status_code == 200, approved.text
    failback = owner.post(
        f"/api/v1/continuity/drills/{drill_id}/failback",
        headers=csrf_headers(owner),
    )
    assert failback.status_code == 200, failback.text
    second_id = failback.json()["failback_request_id"]
    restored = admin.post(
        f"/api/v1/execution/failovers/{second_id}/approve",
        headers=csrf_headers(admin),
        json={"confirmation": "ПЕРЕКЛЮЧИТЬ НА primary"},
    )
    assert restored.status_code == 200, restored.text
    projected = owner.get(f"/api/v1/continuity/drills/{drill_id}")
    assert projected.status_code == 200, projected.text
    assert projected.json()["status"] == ContinuityDrillStatus.AWAITING_SIGNOFF.value
    return drill_id


def test_simulation_never_mutates_lease_or_creates_failover(auth_client: TestClient) -> None:
    """Симуляция produces evidence without changing execution state or network work."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    with auth_client.app.state.session_factory() as db:
        lease = db.query(ExecutionLease).one()
        before = (lease.active_site_key, lease.epoch, lease.status)
    drill_id = _start_simulation(auth_client)
    with auth_client.app.state.session_factory() as db:
        lease = db.query(ExecutionLease).one()
        drill = db.get(ContinuityDrill, drill_id)
        assert (lease.active_site_key, lease.epoch, lease.status) == before
        assert db.query(FailoverRequest).count() == 0
        assert drill is not None
        assert drill.evidence_payload["simulation"] == {
            "network_calls": 0,
            "lease_mutated": False,
            "failover_requests_created": 0,
        }
        primary = db.query(ExecutionSite).filter_by(site_key="primary").one()
        assert primary.details["operator_label"] == "preserve"


def test_live_drill_executes_failover_failback_and_independent_signoff(
    auth_client: TestClient,
) -> None:
    """Живая drill advances two epochs and returns the active lease to primary."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    admin = _create_admin(auth_client, suffix="live")
    try:
        drill_id = _complete_live_drill_without_signoff(auth_client, admin)
        own = auth_client.post(
            f"/api/v1/continuity/drills/{drill_id}/signoff",
            headers=csrf_headers(auth_client),
            json={"accepted": True, "note": "Owner cannot sign own drill"},
        )
        assert own.status_code == 409
        signed = admin.post(
            f"/api/v1/continuity/drills/{drill_id}/signoff",
            headers=csrf_headers(admin),
            json={"accepted": True, "note": "Failover and failback verified"},
        )
        assert signed.status_code == 200, signed.text
        data = signed.json()
        assert data["status"] == ContinuityDrillStatus.PASSED.value
        assert data["target_epoch"] == data["source_epoch"] + 1
        assert data["return_epoch"] == data["target_epoch"] + 1
        overview = admin.get("/api/v1/continuity/overview")
        assert overview.status_code == 200
        assert overview.json()["compliant"] is True
    finally:
        admin.close()
    with auth_client.app.state.session_factory() as db:
        lease = db.query(ExecutionLease).one()
        assert lease.active_site_key == "primary"
        assert lease.status == ExecutionLeaseStatus.ACTIVE


def test_runtime_mismatch_blocks_drill_before_lease_mutation(auth_client: TestClient) -> None:
    """Резервная площадка running another release cannot enter a continuity exercise."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    with auth_client.app.state.session_factory() as db:
        standby = db.query(ExecutionSite).filter_by(site_key="standby").one()
        standby.version = "9.9.9"
        db.commit()
    response = auth_client.post(
        "/api/v1/continuity/drills",
        headers=csrf_headers(auth_client),
        json={"mode": "live", "target_site_key": "standby"},
    )
    assert response.status_code == 409
    assert "версия приложения" in response.json()["detail"]
    with auth_client.app.state.session_factory() as db:
        assert db.query(FailoverRequest).count() == 0
        assert db.query(ExecutionLease).one().status == ExecutionLeaseStatus.ACTIVE


def test_policy_change_invalidates_unfinished_drill(auth_client: TestClient) -> None:
    """Изменение acceptance criteria invalidates drafts bound to the old fingerprint."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    created = auth_client.post(
        "/api/v1/continuity/drills",
        headers=csrf_headers(auth_client),
        json={"mode": "simulation", "target_site_key": "standby"},
    )
    drill_id = created.json()["id"]
    changed = auth_client.patch(
        "/api/v1/continuity/policy",
        headers=csrf_headers(auth_client),
        json={
            "enabled": True,
            "require_live_drill": False,
            "max_rto_seconds": 600,
            "evidence_valid_days": 14,
            "require_distinct_signoff": True,
        },
    )
    assert changed.status_code == 200
    assert (
        auth_client.get(f"/api/v1/continuity/drills/{drill_id}").json()["status"] == "invalidated"
    )


def test_event_chain_detects_payload_tampering(auth_client: TestClient) -> None:
    """Изменение historical payload invalidates the hash-linked event history."""

    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    with auth_client.app.state.session_factory() as db:
        event = db.query(ContinuityDrillEvent).filter_by(drill_id=drill_id).first()
        assert event is not None
        event.payload = {"tampered": True}
        db.commit()
    with auth_client.app.state.session_factory() as db:
        assert verify_event_chain(db, drill_id=drill_id)["valid"] is False


def test_empty_event_history_is_not_valid_evidence(auth_client: TestClient) -> None:
    """Отсутствующая event chain is never treated as an intact empty chain."""

    with auth_client.app.state.session_factory() as db:
        result = verify_event_chain(db, drill_id="missing-drill")
        assert result["valid"] is False
        assert result["checked_events"] == 0
        assert "пуста" in result["error"]


def test_viewer_can_read_but_cannot_create_drill(auth_client: TestClient) -> None:
    """Пользователи только для чтения operators can inspect compliance but cannot mutate it."""

    _register_runtime_pair(auth_client)
    response = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "continuity-viewer@example.com",
            "display_name": "Viewer",
            "password": "ContinuityViewer_123!",
            "role": "viewer",
        },
    )
    assert response.status_code == 201
    viewer = TestClient(auth_client.app)
    try:
        assert (
            viewer.post(
                "/api/v1/auth/login",
                json={
                    "email": "continuity-viewer@example.com",
                    "password": "ContinuityViewer_123!",
                },
            ).status_code
            == 200
        )
        assert viewer.get("/api/v1/continuity/overview").status_code == 200
        denied = viewer.post(
            "/api/v1/continuity/drills",
            headers=csrf_headers(viewer),
            json={"mode": "simulation", "target_site_key": "standby"},
        )
        assert denied.status_code == 403
    finally:
        viewer.close()


def test_platform_required_policy_cannot_be_disabled(auth_client: TestClient) -> None:
    """Организация cannot turn off a continuity gate mandated by deployment settings."""

    auth_client.app.state.settings.continuity_assurance_required = True
    response = auth_client.patch(
        "/api/v1/continuity/policy",
        headers=csrf_headers(auth_client),
        json={
            "enabled": False,
            "require_live_drill": True,
            "max_rto_seconds": 300,
            "evidence_valid_days": 30,
            "require_distinct_signoff": True,
        },
    )
    assert response.status_code == 409


def test_simulation_cannot_satisfy_policy_requiring_live_drill(auth_client: TestClient) -> None:
    """Симуляция evidence remains informative but cannot satisfy a live-only policy."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    drill_id = _start_simulation(auth_client)
    admin = _create_admin(auth_client, suffix="live-required")
    try:
        response = admin.post(
            f"/api/v1/continuity/drills/{drill_id}/signoff",
            headers=csrf_headers(admin),
            json={"accepted": True, "note": "Simulation is not live"},
        )
        assert response.status_code == 409
        assert "live-drill" in response.json()["detail"]
    finally:
        admin.close()


def test_rto_breach_cannot_be_accepted_but_can_be_rejected(auth_client: TestClient) -> None:
    """Превысившие бюджет evidence may be rejected but never falsely accepted."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    update = auth_client.patch(
        "/api/v1/continuity/policy",
        headers=csrf_headers(auth_client),
        json={
            "enabled": True,
            "require_live_drill": True,
            "max_rto_seconds": 30,
            "evidence_valid_days": 30,
            "require_distinct_signoff": True,
        },
    )
    assert update.status_code == 200
    admin = _create_admin(auth_client, suffix="rto")
    try:
        drill_id = _complete_live_drill_without_signoff(auth_client, admin)
        with auth_client.app.state.session_factory() as db:
            drill = db.get(ContinuityDrill, drill_id)
            assert drill is not None and drill.evidence_payload
            drill.rto_seconds = 31
            drill.evidence_payload = {**drill.evidence_payload, "rto_seconds": 31}
            drill.evidence_sha256 = _canonical_sha256(drill.evidence_payload)
            db.commit()
        accepted = admin.post(
            f"/api/v1/continuity/drills/{drill_id}/signoff",
            headers=csrf_headers(admin),
            json={"accepted": True, "note": "RTO exceeded"},
        )
        assert accepted.status_code == 409
        rejected = admin.post(
            f"/api/v1/continuity/drills/{drill_id}/signoff",
            headers=csrf_headers(admin),
            json={"accepted": False, "note": "Rejected due to RTO"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "failed"
    finally:
        admin.close()


def test_expired_signed_evidence_blocks_compliance(auth_client: TestClient) -> None:
    """Принятые evidence stops satisfying the gate immediately after expiry."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    admin = _create_admin(auth_client, suffix="expiry")
    try:
        assert (
            admin.post(
                f"/api/v1/continuity/drills/{drill_id}/signoff",
                headers=csrf_headers(admin),
                json={"accepted": True, "note": "Initially valid"},
            ).status_code
            == 200
        )
    finally:
        admin.close()
    with auth_client.app.state.session_factory() as db:
        drill = db.get(ContinuityDrill, drill_id)
        assert drill is not None
        drill.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    data = auth_client.get("/api/v1/continuity/overview").json()
    assert data["compliant"] is False
    assert "Continuity-evidence просрочено" in data["blockers"]


def test_runtime_evidence_payload_excludes_secret_values(auth_client: TestClient) -> None:
    """Данные runtime fingerprints contain safety switches but never credentials."""

    settings = auth_client.app.state.settings
    payload = critical_config_payload(settings)
    serialized = repr(payload)
    assert "master_key" not in payload
    assert "jwt_secret" not in payload
    assert settings.master_key not in serialized
    assert settings.jwt_secret not in serialized


def test_signoff_event_binds_verified_pre_signoff_chain(auth_client: TestClient) -> None:
    """Событие приёмки event records the exact history hash reviewed by approver."""

    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    with auth_client.app.state.session_factory() as db:
        pre_hash = verify_event_chain(db, drill_id=drill_id)["last_hash"]
    admin = _create_admin(auth_client, suffix="chain")
    try:
        assert (
            admin.post(
                f"/api/v1/continuity/drills/{drill_id}/signoff",
                headers=csrf_headers(admin),
                json={"accepted": True, "note": "Chain verified"},
            ).status_code
            == 200
        )
    finally:
        admin.close()
    with auth_client.app.state.session_factory() as db:
        terminal = (
            db.query(ContinuityDrillEvent)
            .filter_by(drill_id=drill_id, event_type=ContinuityDrillEventType.SIGNED_OFF)
            .one()
        )
        assert terminal.payload["verified_event_chain_last_hash"] == pre_hash


def test_recomputed_but_semantically_wrong_evidence_cannot_be_signed(
    auth_client: TestClient,
) -> None:
    """Пересчёт a plain digest cannot make evidence for another route valid."""

    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    with auth_client.app.state.session_factory() as db:
        drill = db.get(ContinuityDrill, drill_id)
        assert drill is not None and drill.evidence_payload
        drill.evidence_payload = {**drill.evidence_payload, "target_site_key": "other"}
        drill.evidence_sha256 = _canonical_sha256(drill.evidence_payload)
        db.commit()
    admin = _create_admin(auth_client, suffix="semantic")
    try:
        response = admin.post(
            f"/api/v1/continuity/drills/{drill_id}/signoff",
            headers=csrf_headers(admin),
            json={"accepted": True, "note": "Must fail"},
        )
        assert response.status_code == 409
        assert "target_site_key" in response.json()["detail"]
    finally:
        admin.close()


def test_passed_status_without_signoff_metadata_is_not_compliant(auth_client: TestClient) -> None:
    """Статус PASSED status alone never substitutes independent reviewer metadata."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    admin = _create_admin(auth_client, suffix="metadata")
    try:
        assert (
            admin.post(
                f"/api/v1/continuity/drills/{drill_id}/signoff",
                headers=csrf_headers(admin),
                json={"accepted": True, "note": "Initially valid"},
            ).status_code
            == 200
        )
    finally:
        admin.close()
    with auth_client.app.state.session_factory() as db:
        drill = db.get(ContinuityDrill, drill_id)
        assert drill is not None
        drill.signed_off_by_id = None
        db.commit()
    blockers = auth_client.get("/api/v1/continuity/overview").json()["blockers"]
    assert "Continuity-evidence не имеет независимой приёмки" in blockers


def test_completed_failover_requires_matching_active_lease(auth_client: TestClient) -> None:
    """Завершённый request is rejected when the authoritative lease never moved."""

    _register_runtime_pair(auth_client)
    created = auth_client.post(
        "/api/v1/continuity/drills",
        headers=csrf_headers(auth_client),
        json={"mode": "live", "target_site_key": "standby"},
    )
    drill_id = created.json()["id"]
    started = auth_client.post(
        f"/api/v1/continuity/drills/{drill_id}/start",
        headers=csrf_headers(auth_client),
    )
    request_id = started.json()["failover_request_id"]
    with auth_client.app.state.session_factory() as db:
        request = db.get(FailoverRequest, request_id)
        assert request is not None
        request.status = FailoverRequestStatus.COMPLETED
        request.target_epoch = request.source_epoch + 1
        request.completed_at = datetime.now(UTC)
        db.commit()
    projected = auth_client.get(f"/api/v1/continuity/drills/{drill_id}")
    assert projected.status_code == 200
    assert projected.json()["status"] == "failed"
    assert "Execution lease" in projected.json()["failure_reason"]


def test_critical_runtime_hash_changes_with_continuity_controls(auth_client: TestClient) -> None:
    """Различные continuity defaults produce incompatible runtime fingerprints."""

    settings = auth_client.app.state.settings
    first = canonical_sha256(critical_config_payload(settings))
    changed = settings.model_copy(
        update={
            "continuity_default_max_rto_seconds": settings.continuity_default_max_rto_seconds + 1
        }
    )
    assert first != canonical_sha256(critical_config_payload(changed))


def test_event_chain_rejects_cross_tenant_event_after_hash_recalculation(
    auth_client: TestClient,
) -> None:
    """Перенос an event to another tenant cannot produce an acceptable chain."""

    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    with auth_client.app.state.session_factory() as db:
        other = Organization(name="Other", slug="continuity-other")
        db.add(other)
        db.flush()
        event = (
            db.query(ContinuityDrillEvent)
            .filter_by(drill_id=drill_id)
            .order_by(ContinuityDrillEvent.sequence)
            .first()
        )
        assert event is not None
        event.organization_id = other.id
        event.event_hash = _canonical_sha256(
            _event_payload(
                organization_id=other.id,
                drill_id=event.drill_id,
                sequence=event.sequence,
                event_type=event.event_type,
                actor_user_id=event.actor_user_id,
                payload=event.payload,
                previous_hash=event.previous_hash,
                created_at=event.created_at.replace(tzinfo=event.created_at.tzinfo or UTC),
            )
        )
        db.commit()
    with auth_client.app.state.session_factory() as db:
        result = verify_event_chain(db, drill_id=drill_id)
        assert result["valid"] is False
        assert "другой организации" in result["error"]


def test_recomputed_runtime_and_timestamp_evidence_cannot_be_signed(
    auth_client: TestClient,
) -> None:
    """Пересчитанный digest cannot hide runtime and completion-time changes."""

    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    with auth_client.app.state.session_factory() as db:
        drill = db.get(ContinuityDrill, drill_id)
        assert drill is not None and drill.evidence_payload
        drill.evidence_payload = {
            **drill.evidence_payload,
            "runtime_snapshot": {"source": {"runtime_fingerprint": "tampered"}},
            "completed_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        drill.evidence_sha256 = _canonical_sha256(drill.evidence_payload)
        db.commit()
    admin = _create_admin(auth_client, suffix="runtime-tamper")
    try:
        response = admin.post(
            f"/api/v1/continuity/drills/{drill_id}/signoff",
            headers=csrf_headers(admin),
            json={"accepted": True, "note": "Must fail"},
        )
        assert response.status_code == 409
        assert "runtime_snapshot" in response.json()["detail"]
        assert "completed_at" in response.json()["detail"]
    finally:
        admin.close()


def test_compliance_rechecks_pre_signoff_chain_hash_semantics(auth_client: TestClient) -> None:
    """Повторно хешированное terminal event cannot claim review of a different history."""

    auth_client.app.state.settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    admin = _create_admin(auth_client, suffix="terminal")
    try:
        assert (
            admin.post(
                f"/api/v1/continuity/drills/{drill_id}/signoff",
                headers=csrf_headers(admin),
                json={"accepted": True, "note": "Initially valid"},
            ).status_code
            == 200
        )
    finally:
        admin.close()
    with auth_client.app.state.session_factory() as db:
        terminal = (
            db.query(ContinuityDrillEvent)
            .filter_by(drill_id=drill_id)
            .order_by(ContinuityDrillEvent.sequence.desc())
            .first()
        )
        assert terminal is not None
        terminal.payload = {**terminal.payload, "verified_event_chain_last_hash": "f" * 64}
        terminal.event_hash = _canonical_sha256(
            _event_payload(
                organization_id=terminal.organization_id,
                drill_id=terminal.drill_id,
                sequence=terminal.sequence,
                event_type=terminal.event_type,
                actor_user_id=terminal.actor_user_id,
                payload=terminal.payload,
                previous_hash=terminal.previous_hash,
                created_at=terminal.created_at.replace(tzinfo=terminal.created_at.tzinfo or UTC),
            )
        )
        db.commit()
        assert verify_event_chain(db, drill_id=drill_id)["valid"] is True
    blockers = auth_client.get("/api/v1/continuity/overview").json()["blockers"]
    assert any("проверенный до приёмки" in item for item in blockers)


def test_compliance_selects_healthy_standby_when_first_candidate_is_stale(
    auth_client: TestClient,
) -> None:
    """Устаревшая first standby does not hide another fresh compatible site."""

    settings = auth_client.app.state.settings
    settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    organization_id = _organization_id(auth_client)
    now = datetime.now(UTC)
    with auth_client.app.state.session_factory() as db:
        stale = db.query(ExecutionSite).filter_by(site_key="standby").one()
        stale.last_seen_at = now - timedelta(hours=1)
        upsert_site_heartbeat(
            db,
            organization_id=organization_id,
            settings=settings.model_copy(
                update={"execution_site_key": "standby-z", "execution_site_name": "Healthy standby"}
            ),
            worker_id="healthy-worker",
            details={"role": "standby"},
            now=now,
        )
        db.commit()
    with auth_client.app.state.session_factory() as db:
        result = continuity_compliance(
            db, organization_id=organization_id, settings=settings, now=now
        )
        assert result.runtime_snapshot["standby"]["site_key"] == "standby-z"
        assert "Standby-площадка не имеет свежего heartbeat" not in result.blockers


def test_get_or_create_policy_reuses_single_tenant_row(auth_client: TestClient) -> None:
    """Повторная initialization returns the same unique continuity policy row."""

    organization_id = _organization_id(auth_client)
    with auth_client.app.state.session_factory() as db:
        first = get_or_create_policy(
            db, organization_id=organization_id, settings=auth_client.app.state.settings
        )
        second = get_or_create_policy(
            db, organization_id=organization_id, settings=auth_client.app.state.settings
        )
        assert first.id == second.id
        assert db.query(ContinuityPolicy).filter_by(organization_id=organization_id).count() == 1


def test_live_drill_cannot_be_cancelled_after_target_becomes_active(
    auth_client: TestClient,
) -> None:
    """Операторы must fail back before closing a drill that moved the active lease."""

    _register_runtime_pair(auth_client)
    admin = _create_admin(auth_client, suffix="cancel-live")
    try:
        created = auth_client.post(
            "/api/v1/continuity/drills",
            headers=csrf_headers(auth_client),
            json={"mode": "live", "target_site_key": "standby"},
        )
        drill_id = created.json()["id"]
        started = auth_client.post(
            f"/api/v1/continuity/drills/{drill_id}/start",
            headers=csrf_headers(auth_client),
        )
        request_id = started.json()["failover_request_id"]
        assert (
            admin.post(
                f"/api/v1/execution/failovers/{request_id}/approve",
                headers=csrf_headers(admin),
                json={"confirmation": "ПЕРЕКЛЮЧИТЬ НА standby"},
            ).status_code
            == 200
        )
        response = auth_client.post(
            f"/api/v1/continuity/drills/{drill_id}/cancel",
            headers=csrf_headers(auth_client),
        )
        assert response.status_code == 409
        assert "failback" in response.json()["detail"]
    finally:
        admin.close()


def test_only_one_unfinished_drill_is_allowed(auth_client: TestClient) -> None:
    """Организация cannot run overlapping exercises against one execution lease."""

    _register_runtime_pair(auth_client)
    first = auth_client.post(
        "/api/v1/continuity/drills",
        headers=csrf_headers(auth_client),
        json={"mode": "simulation", "target_site_key": "standby"},
    )
    assert first.status_code == 201
    second = auth_client.post(
        "/api/v1/continuity/drills",
        headers=csrf_headers(auth_client),
        json={"mode": "live", "target_site_key": "standby"},
    )
    assert second.status_code == 409


def test_background_sync_projects_completed_failover_without_creating_requests(
    auth_client: TestClient,
) -> None:
    """Фоновый worker projection advances a completed request without initiating new work."""

    _register_runtime_pair(auth_client)
    admin = _create_admin(auth_client, suffix="background-sync")
    try:
        created = auth_client.post(
            "/api/v1/continuity/drills",
            headers=csrf_headers(auth_client),
            json={"mode": "live", "target_site_key": "standby"},
        )
        assert created.status_code == 201, created.text
        drill_id = created.json()["id"]
        started = auth_client.post(
            f"/api/v1/continuity/drills/{drill_id}/start",
            headers=csrf_headers(auth_client),
        )
        request_id = started.json()["failover_request_id"]
        assert (
            admin.post(
                f"/api/v1/execution/failovers/{request_id}/approve",
                headers=csrf_headers(admin),
                json={"confirmation": "ПЕРЕКЛЮЧИТЬ НА standby"},
            ).status_code
            == 200
        )
        with auth_client.app.state.session_factory() as db:
            before_requests = db.query(FailoverRequest).count()
            summary = synchronize_open_drills(db, settings=auth_client.app.state.settings)
            db.commit()
            drill = db.get(ContinuityDrill, drill_id)
            assert drill is not None
            assert drill.status == ContinuityDrillStatus.AWAITING_FAILBACK
            assert summary.scanned == 1
            assert summary.updated == 1
            assert summary.errors == 0
            assert db.query(FailoverRequest).count() == before_requests
    finally:
        admin.close()


def test_background_sync_isolates_a_failed_drill_with_savepoint(
    auth_client: TestClient, monkeypatch
) -> None:
    """Одна повреждённая drill cannot roll back another tenant's successful projection."""

    from app.services import continuity as continuity_service

    with auth_client.app.state.session_factory() as db:
        owner = db.query(User).filter(User.email == "owner@example.com").one()
        second = Organization(name="Continuity Sync Other", slug="continuity-sync-other")
        db.add(second)
        db.flush()
        first = ContinuityDrill(
            organization_id=owner.organization_id,
            mode=ContinuityDrillMode.LIVE,
            status=ContinuityDrillStatus.RUNNING,
            source_site_key="primary",
            target_site_key="standby",
            source_epoch=1,
            policy_snapshot={},
            policy_sha256="a" * 64,
            runtime_snapshot={},
        )
        good = ContinuityDrill(
            organization_id=second.id,
            mode=ContinuityDrillMode.LIVE,
            status=ContinuityDrillStatus.RUNNING,
            source_site_key="primary",
            target_site_key="standby",
            source_epoch=1,
            policy_snapshot={},
            policy_sha256="b" * 64,
            runtime_snapshot={},
        )
        db.add_all([first, good])
        db.commit()
        first_id, good_id = first.id, good.id

    original = continuity_service.synchronize_drill

    def fake_synchronize(db, *, drill, settings, actor, now):
        """Выбросить исключение for one row and deterministically advance the other row."""

        del db, settings, actor, now
        if drill.id == first_id:
            raise RuntimeError("broken drill")
        drill.status = ContinuityDrillStatus.AWAITING_FAILBACK
        return drill

    monkeypatch.setattr(continuity_service, "synchronize_drill", fake_synchronize)
    try:
        with auth_client.app.state.session_factory() as db:
            summary = synchronize_open_drills(db, settings=auth_client.app.state.settings)
            db.commit()
            assert summary.scanned == 2
            assert summary.updated == 1
            assert summary.errors == 1
            assert db.get(ContinuityDrill, first_id).status == ContinuityDrillStatus.RUNNING
            assert (
                db.get(ContinuityDrill, good_id).status == ContinuityDrillStatus.AWAITING_FAILBACK
            )
    finally:
        monkeypatch.setattr(continuity_service, "synchronize_drill", original)


def test_continuity_metrics_reflect_blocked_and_compliant_states(
    auth_client: TestClient,
) -> None:
    """Проверить метрики совместимости, свежести evidence и числа continuity-drill."""

    settings = auth_client.app.state.settings
    settings.continuity_assurance_required = True
    _register_runtime_pair(auth_client)
    blocked = auth_client.get("/metrics")
    assert blocked.status_code == 200
    assert 'teleflow_continuity_compliance{state="blocked"} 1.0' in blocked.text
    assert "teleflow_continuity_stale_evidence 1.0" in blocked.text

    _set_simulation_policy(auth_client)
    drill_id = _start_simulation(auth_client)
    admin = _create_admin(auth_client, suffix="metrics")
    try:
        signed = admin.post(
            f"/api/v1/continuity/drills/{drill_id}/signoff",
            headers=csrf_headers(admin),
            json={"accepted": True, "note": "Metrics evidence"},
        )
        assert signed.status_code == 200, signed.text
    finally:
        admin.close()
    current = auth_client.get("/metrics")
    assert current.status_code == 200
    assert 'teleflow_continuity_compliance{state="compliant"} 1.0' in current.text
    assert "teleflow_continuity_stale_evidence 0.0" in current.text
    assert 'teleflow_continuity_drills{mode="simulation",status="passed"} 1.0' in current.text
    assert "teleflow_continuity_rto_breaches 0.0" in current.text
