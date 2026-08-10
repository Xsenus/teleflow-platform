from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit import ZERO_HASH, backfill_audit_chain, verify_audit_chain
from app.enums import CampaignApprovalStatus, CampaignStatus, JobStatus
from app.models import (
    AuditChainState,
    AuditLog,
    Campaign,
    CampaignApprovalRequest,
    DeliveryJob,
    TelegramConnection,
)
from app.services.crypto import SecretCipher
from app.services.key_rotation import rotate_master_key
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)

OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "OwnerPassword_123!"
ADMIN_EMAIL = "reviewer@example.com"
ADMIN_PASSWORD = "ReviewerPassword_123!"
SECOND_ADMIN_EMAIL = "second-reviewer@example.com"
SECOND_ADMIN_PASSWORD = "SecondReviewer_123!"


def login(client: TestClient, email: str, password: str) -> dict:
    """Выполнить операцию login. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    client.cookies.clear()
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def create_admin(client: TestClient, email: str, password: str, name: str) -> dict:
    """Создать admin. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    response = client.post(
        "/api/v1/users",
        headers=csrf_headers(client),
        json={
            "email": email,
            "display_name": name,
            "password": password,
            "role": "admin",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_ready_campaign(
    client: TestClient, suffix: str = "governance"
) -> tuple[dict, dict, dict, dict]:
    """Создать ready campaign. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    connection = create_connection(client)
    destination = create_destination(client, connection["id"])
    # Avoid duplicate usernames when a test creates more than one destination.
    if suffix != "governance":
        patched = client.patch(
            f"/api/v1/destinations/{destination['id']}",
            headers=csrf_headers(client),
            json={"title": f"Allowed {suffix}"},
        )
        assert patched.status_code == 200, patched.text
        destination = patched.json()
    template = create_template(client)
    campaign = create_campaign(client, connection["id"], template["id"], destination["id"])
    return connection, destination, template, campaign


def test_run_now_requires_explicit_current_approval(auth_client: TestClient) -> None:
    """Проверить сценарий run now requires explicit current approval. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = create_ready_campaign(auth_client)

    blocked = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert blocked.status_code == 409
    assert "утверждение" in blocked.text.lower()

    request = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/submit-approval",
        headers=csrf_headers(auth_client),
        json={"note": "Проверен маршрут и содержание"},
    )
    assert request.status_code == 200, request.text
    approval = request.json()
    assert approval["status"] == "pending"
    assert approval["required_approvals"] == 1

    decision = auth_client.post(
        f"/api/v1/campaigns/approval-requests/{approval['id']}/decision",
        headers=csrf_headers(auth_client),
        json={"decision": "approve", "note": "Разрешаю запуск"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"

    allowed = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["approved_at"] is not None


def test_four_eyes_policy_blocks_requester_and_accepts_independent_admin(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий four eyes policy blocks requester and accepts independent admin. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    create_admin(auth_client, ADMIN_EMAIL, ADMIN_PASSWORD, "Independent Reviewer")
    patch = auth_client.patch(
        "/api/v1/organization",
        headers=csrf_headers(auth_client),
        json={"require_distinct_campaign_approver": True},
    )
    assert patch.status_code == 200, patch.text
    _connection, _destination, _template, campaign = create_ready_campaign(auth_client)

    submitted = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/submit-approval",
        headers=csrf_headers(auth_client),
        json={"note": "Нужна независимая проверка"},
    )
    assert submitted.status_code == 200, submitted.text
    request_id = submitted.json()["id"]
    assert submitted.json()["require_distinct_requester"] is True

    self_decision = auth_client.post(
        f"/api/v1/campaigns/approval-requests/{request_id}/decision",
        headers=csrf_headers(auth_client),
        json={"decision": "approve"},
    )
    assert self_decision.status_code == 409

    login(auth_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    independent = auth_client.post(
        f"/api/v1/campaigns/approval-requests/{request_id}/decision",
        headers=csrf_headers(auth_client),
        json={"decision": "approve", "note": "Проверено вторым администратором"},
    )
    assert independent.status_code == 200, independent.text
    assert independent.json()["status"] == "approved"

    campaign_read = auth_client.get(f"/api/v1/campaigns/{campaign['id']}")
    assert campaign_read.status_code == 200
    assert campaign_read.json()["approved_at"] is not None


def test_high_risk_campaign_requires_two_distinct_decisions(auth_client: TestClient) -> None:
    """Проверить сценарий high risk campaign requires two distinct decisions. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    create_admin(auth_client, ADMIN_EMAIL, ADMIN_PASSWORD, "Reviewer One")
    create_admin(auth_client, SECOND_ADMIN_EMAIL, SECOND_ADMIN_PASSWORD, "Reviewer Two")
    patch = auth_client.patch(
        "/api/v1/organization",
        headers=csrf_headers(auth_client),
        json={
            "high_risk_destination_threshold": 1,
            "high_risk_required_approvals": 2,
            "require_distinct_campaign_approver": False,
        },
    )
    assert patch.status_code == 200, patch.text
    _connection, _destination, _template, campaign = create_ready_campaign(auth_client)

    submitted = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/submit-approval",
        headers=csrf_headers(auth_client),
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    approval = submitted.json()
    assert approval["required_approvals"] == 2

    first = auth_client.post(
        f"/api/v1/campaigns/approval-requests/{approval['id']}/decision",
        headers=csrf_headers(auth_client),
        json={"decision": "approve"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending"
    assert len(first.json()["decisions"]) == 1

    login(auth_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    second = auth_client.post(
        f"/api/v1/campaigns/approval-requests/{approval['id']}/decision",
        headers=csrf_headers(auth_client),
        json={"decision": "approve"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"
    assert len(second.json()["decisions"]) == 2


def test_approval_fingerprint_becomes_stale_when_destination_rules_change(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий approval fingerprint becomes stale when destination rules change. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _connection, destination, _template, campaign = create_ready_campaign(auth_client)
    approve_campaign(auth_client, campaign["id"])

    changed = auth_client.patch(
        f"/api/v1/destinations/{destination['id']}",
        headers=csrf_headers(auth_client),
        json={"permission_note": "Правила группы обновлены; требуется новая проверка"},
    )
    assert changed.status_code == 200, changed.text

    blocked = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert blocked.status_code == 409
    assert "актуальное утверждение" in blocked.text.lower()


def test_organization_emergency_stop_holds_and_releases_jobs(auth_client: TestClient) -> None:
    """Проверить сценарий organization emergency stop holds and releases jobs. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = create_ready_campaign(auth_client)
    approve_campaign(auth_client, campaign["id"])
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{campaign['id']}/run-now",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )

    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    now = datetime.now(UTC) + timedelta(seconds=2)
    assert scheduler.tick(now=now) == 1

    paused = auth_client.post(
        "/api/v1/organization/publishing/pause",
        headers=csrf_headers(auth_client),
        json={"reason": "Проверка инцидента безопасности"},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["publishing_paused"] is True

    with auth_client.app.state.session_factory() as db:
        job = db.scalar(select(DeliveryJob).where(DeliveryJob.campaign_id == campaign["id"]))
        assert job is not None
        assert job.status == JobStatus.WAITING_REVIEW
        assert job.error_code == "ORG_EMERGENCY_STOP"

    blocked_run = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert blocked_run.status_code == 409

    resumed = auth_client.post(
        "/api/v1/organization/publishing/resume",
        headers=csrf_headers(auth_client),
        json={"note": "Инцидент закрыт"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["publishing_paused"] is False

    with auth_client.app.state.session_factory() as db:
        job = db.scalar(select(DeliveryJob).where(DeliveryJob.campaign_id == campaign["id"]))
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.error_code is None


def test_scheduler_skips_campaigns_while_organization_is_paused(auth_client: TestClient) -> None:
    """Проверить сценарий scheduler skips campaigns while organization is paused. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = create_ready_campaign(auth_client)
    approve_campaign(auth_client, campaign["id"])
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{campaign['id']}/run-now",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )
    assert (
        auth_client.post(
            "/api/v1/organization/publishing/pause",
            headers=csrf_headers(auth_client),
            json={"reason": "Плановая аварийная проверка"},
        ).status_code
        == 200
    )

    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=5)) == 0


def test_notification_dedup_read_and_acknowledge(auth_client: TestClient) -> None:
    """Проверить сценарий notification dedup read and acknowledge. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    for reason in ("Первичное срабатывание защиты", "Повторное срабатывание защиты"):
        response = auth_client.post(
            "/api/v1/organization/publishing/pause",
            headers=csrf_headers(auth_client),
            json={"reason": reason},
        )
        assert response.status_code == 200, response.text

    counts = auth_client.get("/api/v1/notifications/counts")
    assert counts.status_code == 200
    assert counts.json()["unread"] >= 1
    assert counts.json()["critical_unacknowledged"] == 1

    notifications = auth_client.get("/api/v1/notifications?severity=critical")
    assert notifications.status_code == 200
    pause_items = [
        item
        for item in notifications.json()
        if item["event_type"] == "safety.organization_publishing_paused"
    ]
    assert len(pause_items) == 1
    item = pause_items[0]
    assert item["occurrence_count"] == 2

    read = auth_client.post(
        f"/api/v1/notifications/{item['id']}/read",
        headers=csrf_headers(auth_client),
    )
    assert read.status_code == 200
    assert read.json()["status"] == "read"

    acknowledged = auth_client.post(
        f"/api/v1/notifications/{item['id']}/acknowledge",
        headers=csrf_headers(auth_client),
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "acknowledged"
    assert auth_client.get("/api/v1/notifications/counts").json()["critical_unacknowledged"] == 0


def test_audit_hash_chain_detects_tampering(auth_client: TestClient) -> None:
    """Проверить сценарий audit hash chain detects tampering. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    create_connection(auth_client)
    verified = auth_client.get("/api/v1/audit/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["valid"] is True
    assert verified.json()["checked_entries"] >= 2

    with auth_client.app.state.session_factory() as db:
        item = db.scalar(
            select(AuditLog)
            .where(AuditLog.organization_id == auth_client.get("/api/v1/organization").json()["id"])
            .order_by(AuditLog.sequence)
            .limit(1)
        )
        assert item is not None
        item.details = {"tampered": True}
        db.commit()

    invalid = auth_client.get("/api/v1/audit/verify")
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert invalid.json()["first_error_entry_id"] is not None


def test_audit_chain_backfill_rebuilds_legacy_metadata(auth_client: TestClient) -> None:
    """Проверить сценарий audit chain backfill rebuilds legacy metadata. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    create_connection(auth_client)
    organization_id = auth_client.get("/api/v1/organization").json()["id"]
    with auth_client.app.state.session_factory() as db:
        logs = list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.organization_id == organization_id)
                .order_by(AuditLog.created_at, AuditLog.id)
            ).all()
        )
        assert logs
        for item in logs:
            item.sequence = None
            item.prev_hash = None
            item.entry_hash = None
            item.chain_version = None
        state = db.get(AuditChainState, organization_id)
        assert state is not None
        state.last_sequence = 0
        state.last_hash = ZERO_HASH
        db.commit()

        before = verify_audit_chain(db, organization_id=organization_id)
        assert before.valid is False
        assert before.legacy_entries == len(logs)
        rebuilt = backfill_audit_chain(db, organization_id=organization_id)
        db.commit()
        assert rebuilt.valid is True
        assert rebuilt.checked_entries == len(logs)


def test_master_key_rotation_preflight_and_reencrypt(auth_client: TestClient) -> None:
    """Проверить сценарий master key rotation preflight and reencrypt. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    old_cipher = auth_client.app.state.cipher
    new_cipher = SecretCipher("new-master-key-with-sufficient-entropy-9876543210")

    with auth_client.app.state.session_factory() as db:
        model = db.get(TelegramConnection, connection["id"])
        assert model is not None and model.credentials_enc
        original = model.credentials_enc

        preflight = rotate_master_key(
            db,
            old_cipher=old_cipher,
            new_cipher=new_cipher,
            storage=auth_client.app.state.storage,
            dry_run=True,
        )
        assert preflight.total_values >= 1
        assert model.credentials_enc == original
        db.rollback()

    with auth_client.app.state.session_factory() as db:
        report = rotate_master_key(
            db,
            old_cipher=old_cipher,
            new_cipher=new_cipher,
            storage=auth_client.app.state.storage,
            dry_run=False,
        )
        assert report.fields["telegram_connection.credentials"] == 1
        db.commit()

    with auth_client.app.state.session_factory() as db:
        model = db.get(TelegramConnection, connection["id"])
        assert model is not None and model.credentials_enc
        payload = new_cipher.decrypt_json(
            model.credentials_enc, context=f"telegram-connection:{model.id}"
        )
        assert payload["bot_token"].startswith("1234567890:")
        with pytest.raises((InvalidTag, ValueError)):
            old_cipher.decrypt_json(
                model.credentials_enc, context=f"telegram-connection:{model.id}"
            )

        system_chain = verify_audit_chain(db, organization_id=None)
        assert system_chain.valid is True
        assert system_chain.checked_entries >= 1


def test_dashboard_exposes_governance_counters(auth_client: TestClient) -> None:
    """Проверить сценарий dashboard exposes governance counters. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = create_ready_campaign(auth_client)
    submitted = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/submit-approval",
        headers=csrf_headers(auth_client),
        json={},
    )
    assert submitted.status_code == 200
    summary = auth_client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200, summary.text
    payload = summary.json()
    assert payload["approvals_pending"] == 1
    assert payload["notifications_unread"] >= 1
    assert payload["publishing_paused"] is False


def test_scheduler_proactively_expires_approval_request(auth_client: TestClient) -> None:
    """Проверить сценарий scheduler proactively expires approval request. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = create_ready_campaign(auth_client)
    submitted = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/submit-approval",
        headers=csrf_headers(auth_client),
        json={"note": "Короткоживущий запрос"},
    )
    assert submitted.status_code == 200, submitted.text
    request_id = submitted.json()["id"]

    with auth_client.app.state.session_factory() as db:
        approval = db.get(CampaignApprovalRequest, request_id)
        assert approval is not None
        approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=datetime.now(UTC)) == 0

    with auth_client.app.state.session_factory() as db:
        approval = db.get(CampaignApprovalRequest, request_id)
        model = db.get(Campaign, campaign["id"])
        assert approval is not None and approval.status == CampaignApprovalStatus.EXPIRED
        assert model is not None and model.status == CampaignStatus.DRAFT
        assert model.active_approval_request_id is None
        assert model.approved_fingerprint is None

    notices = auth_client.get("/api/v1/notifications")
    assert notices.status_code == 200
    assert any(
        item["event_type"] == "campaign.approval_expired" and item["entity_id"] == campaign["id"]
        for item in notices.json()
    )


def test_replacing_campaign_route_cancels_pending_approval(auth_client: TestClient) -> None:
    """Проверить сценарий replacing campaign route cancels pending approval. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection, _destination, _template, campaign = create_ready_campaign(auth_client)
    submitted = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/submit-approval",
        headers=csrf_headers(auth_client),
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    request_id = submitted.json()["id"]

    second = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "username": "allowed_jobs_group_second",
            "kind": "supergroup",
            "permission_confirmed": True,
            "permission_note": "Разрешено администратором второй тестовой группы",
        },
    )
    assert second.status_code == 201, second.text
    replaced = auth_client.put(
        f"/api/v1/campaigns/{campaign['id']}/destinations",
        headers=csrf_headers(auth_client),
        json={"destination_ids": [second.json()["id"]]},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["active_approval_request_id"] is None

    with auth_client.app.state.session_factory() as db:
        approval = db.get(CampaignApprovalRequest, request_id)
        assert approval is not None
        assert approval.status == CampaignApprovalStatus.CANCELLED


def test_audit_verification_fails_when_chain_state_is_missing(auth_client: TestClient) -> None:
    """Проверить сценарий audit verification fails when chain state is missing. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    create_connection(auth_client)
    organization_id = auth_client.get("/api/v1/organization").json()["id"]
    with auth_client.app.state.session_factory() as db:
        state = db.get(AuditChainState, organization_id)
        assert state is not None
        db.delete(state)
        db.commit()

        result = verify_audit_chain(db, organization_id=organization_id)
        assert result.valid is False
        assert result.first_error == "Отсутствует состояние hash-chain"


def test_compatibility_approve_rejects_unfulfillable_high_risk_policy(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий compatibility approve rejects unfulfillable high risk policy. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    reviewer = create_admin(
        auth_client,
        ADMIN_EMAIL,
        ADMIN_PASSWORD,
        "Temporary Reviewer",
    )
    patch = auth_client.patch(
        "/api/v1/organization",
        headers=csrf_headers(auth_client),
        json={
            "high_risk_destination_threshold": 1,
            "high_risk_required_approvals": 2,
            "require_distinct_campaign_approver": False,
        },
    )
    assert patch.status_code == 200, patch.text

    deactivated = auth_client.delete(
        f"/api/v1/users/{reviewer['id']}",
        headers=csrf_headers(auth_client),
    )
    assert deactivated.status_code == 200, deactivated.text
    _connection, _destination, _template, campaign = create_ready_campaign(auth_client)

    response = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/approve",
        headers=csrf_headers(auth_client),
    )
    assert response.status_code == 409
    assert "недостаточно" in response.text.lower()

    with auth_client.app.state.session_factory() as db:
        requests = list(
            db.scalars(
                select(CampaignApprovalRequest).where(
                    CampaignApprovalRequest.campaign_id == campaign["id"]
                )
            ).all()
        )
        assert requests == []
