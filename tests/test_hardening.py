from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import ChallengeStatus, ConnectionStatus, JobStatus
from app.main import create_app
from app.models import (
    AuditLog,
    CampaignDestination,
    DeliveryJob,
    TelegramAuthChallenge,
    TelegramConnection,
)
from app.services.delivery import DeliveryService
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import approve_campaign, create_campaign, create_template


def _create_bot(client: TestClient, name: str, *, daily_cap: int = 1) -> dict:
    """Реализовать внутренний этап create bot step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    response = client.post(
        "/api/v1/connections/bot",
        headers=csrf_headers(client),
        json={
            "name": name,
            "bot_token": f"1234567890:{name.lower().replace(' ', '-')}-abcdefghijklmnopqrstuvwxyz",
            "min_interval_seconds": 1,
            "daily_cap": daily_cap,
            "destination_cooldown_minutes": 60,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_allowed_destination(client: TestClient, connection_id: str, username: str) -> dict:
    """Реализовать внутренний этап create allowed destination step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    response = client.post(
        "/api/v1/destinations",
        headers=csrf_headers(client),
        json={
            "connection_id": connection_id,
            "username": username,
            "kind": "supergroup",
            "permission_confirmed": True,
            "permission_note": "Публикация согласована с администратором",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_totp_failures_use_the_same_lockout_counter(client: TestClient, monkeypatch) -> None:
    """Проверить сценарий totp failures use the same lockout counter. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    monkeypatch.setattr("app.api.auth.verify_user_totp", lambda *_args, **_kwargs: False)
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "OwnerPassword_123!",
                "totp_code": "123456",
            },
        )
        assert response.status_code == 401
    locked = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "OwnerPassword_123!"},
    )
    assert locked.status_code == 423


def test_refresh_token_rotation_rejects_replay(client: TestClient) -> None:
    """Проверить сценарий refresh token rotation rejects replay. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "OwnerPassword_123!"},
    )
    assert login.status_code == 200
    old_refresh = client.cookies.get("teleflow_refresh")
    assert old_refresh
    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    client.cookies.set("teleflow_refresh", old_refresh)
    replay = client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401


def test_forwarded_for_is_ignored_without_trusted_proxy(client: TestClient) -> None:
    """Проверить сценарий forwarded for is ignored without trusted proxy. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    response = client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "203.0.113.77"},
        json={"email": "owner@example.com", "password": "OwnerPassword_123!"},
    )
    assert response.status_code == 200
    with client.app.state.session_factory() as db:
        item = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == "auth.login_succeeded")
            .order_by(AuditLog.created_at.desc())
        )
        assert item is not None
        assert item.ip_address != "203.0.113.77"


def test_forwarded_for_is_used_only_when_proxy_is_trusted(settings, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий forwarded for is used only when proxy is trusted. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    trusted = settings.model_copy(
        update={
            "database_url": f"sqlite:///{tmp_path / 'trusted-proxy.db'}",
            "storage_path": tmp_path / "trusted-storage",
            "trust_proxy_headers": True,
        }
    )
    with TestClient(create_app(trusted)) as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": "203.0.113.88"},
            json={"email": "owner@example.com", "password": "OwnerPassword_123!"},
        )
        assert response.status_code == 200
        with client.app.state.session_factory() as db:
            item = db.scalar(
                select(AuditLog)
                .where(AuditLog.action == "auth.login_succeeded")
                .order_by(AuditLog.created_at.desc())
            )
            assert item is not None
            assert item.ip_address == "203.0.113.88"


def test_static_ui_obeys_strict_csp(client: TestClient) -> None:
    """Проверить сценарий static ui obeys strict csp. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    response = client.get("/")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "unsafe-inline" not in csp
    js = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text("utf-8")
    assert "style=" not in js.lower()


def test_media_signature_and_utf8_are_validated(auth_client: TestClient) -> None:
    """Проверить сценарий media signature and utf8 are validated. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    fake_png = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("fake.png", b"this is not a png", "image/png")},
    )
    assert fake_png.status_code == 415

    invalid_text = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("bad.txt", b"\xff\xfe\x00", "text/plain")},
    )
    assert invalid_text.status_code == 415


def test_media_caption_limit_is_enforced_on_create_and_patch(auth_client: TestClient) -> None:
    """Проверить сценарий media caption limit is enforced on create and patch. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    upload = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("note.txt", b"safe text", "text/plain")},
    )
    assert upload.status_code == 201
    asset_id = upload.json()["id"]

    too_long = auth_client.post(
        "/api/v1/templates",
        headers=csrf_headers(auth_client),
        json={"name": "Long caption", "body": "x" * 1025, "media_asset_id": asset_id},
    )
    assert too_long.status_code == 422

    plain = auth_client.post(
        "/api/v1/templates",
        headers=csrf_headers(auth_client),
        json={"name": "Long plain", "body": "x" * 2000},
    )
    assert plain.status_code == 201
    patched = auth_client.patch(
        f"/api/v1/templates/{plain.json()['id']}",
        headers=csrf_headers(auth_client),
        json={"media_asset_id": asset_id},
    )
    assert patched.status_code == 422


def test_campaign_with_all_links_disabled_is_blocked(auth_client: TestClient) -> None:
    """Проверить сценарий campaign with all links disabled is blocked. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = _create_bot(auth_client, "Disabled Links", daily_cap=5)
    destination = _create_allowed_destination(auth_client, connection["id"], "disabled_links_group")
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    with auth_client.app.state.session_factory() as db:
        link = db.scalar(
            select(CampaignDestination).where(CampaignDestination.campaign_id == campaign["id"])
        )
        assert link is not None
        link.enabled = False
        db.commit()
    preview = auth_client.get(f"/api/v1/campaigns/{campaign['id']}/preview")
    assert preview.status_code == 200
    assert preview.json()["valid"] is False
    assert any("Все назначения" in item for item in preview.json()["blockers"])


def test_global_daily_cap_applies_across_connections(auth_client: TestClient) -> None:
    """Проверить сценарий global daily cap applies across connections. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    auth_client.app.state.settings.global_hard_daily_cap = 1
    now = datetime.now(UTC) + timedelta(seconds=2)

    first = _create_bot(auth_client, "First Bot")
    first_destination = _create_allowed_destination(auth_client, first["id"], "global_cap_first")
    first_template = create_template(auth_client)
    first_campaign = create_campaign(
        auth_client, first["id"], first_template["id"], first_destination["id"]
    )
    approve_campaign(auth_client, first_campaign["id"])
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{first_campaign['id']}/run-now",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=now) == 1
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="global-cap-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))

    second = _create_bot(auth_client, "Second Bot")
    second_destination = _create_allowed_destination(auth_client, second["id"], "global_cap_second")
    second_template = create_template(auth_client)
    second_campaign = create_campaign(
        auth_client, second["id"], second_template["id"], second_destination["id"]
    )
    approve_campaign(auth_client, second_campaign["id"])
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{second_campaign['id']}/run-now",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )
    assert scheduler.tick(now=now + timedelta(seconds=2)) == 1
    assert delivery.process_next(now=now + timedelta(seconds=3))

    with auth_client.app.state.session_factory() as db:
        job = db.scalar(select(DeliveryJob).where(DeliveryJob.campaign_id == second_campaign["id"]))
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.safety_decision is not None
        assert job.safety_decision["code"] == "GLOBAL_DAILY_CAP_REACHED"


def test_expired_mtproto_challenge_is_scrubbed(auth_client: TestClient) -> None:
    """Проверить сценарий expired mtproto challenge is scrubbed. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    started = auth_client.post(
        "/api/v1/connections/user/start",
        headers=csrf_headers(auth_client),
        json={
            "name": "User Account",
            "api_id": 123456,
            "api_hash": "0123456789abcdef0123456789abcdef",
            "phone": "+31612345678",
        },
    )
    assert started.status_code == 201, started.text
    challenge_id = started.json()["challenge_id"]
    connection_id = started.json()["connection_id"]
    with auth_client.app.state.session_factory() as db:
        challenge = db.get(TelegramAuthChallenge, challenge_id)
        assert challenge is not None
        challenge.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    completed = auth_client.post(
        "/api/v1/connections/user/complete",
        headers=csrf_headers(auth_client),
        json={"challenge_id": challenge_id, "code": "12345"},
    )
    assert completed.status_code == 410
    with auth_client.app.state.session_factory() as db:
        challenge = db.get(TelegramAuthChallenge, challenge_id)
        connection = db.get(TelegramConnection, connection_id)
        assert challenge is not None and connection is not None
        assert challenge.status == ChallengeStatus.EXPIRED
        payload = auth_client.app.state.cipher.decrypt_json(
            challenge.payload_enc, context=f"telegram-auth-challenge:{challenge.id}"
        )
        assert payload == {"expired": True}
        assert connection.status == ConnectionStatus.ERROR
        assert connection.credentials_enc is None
