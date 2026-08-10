from __future__ import annotations

import hashlib
import io
import json
import warnings
import zipfile
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import (
    ConfigurationBundleKind,
    ConnectionStatus,
    PermissionStatus,
    PilotProgramStatus,
    PilotStageStatus,
    UserRole,
)
from app.models import (
    AuditLog,
    Campaign,
    CampaignRun,
    ConfigurationBundle,
    Destination,
    MediaAsset,
    Organization,
    PilotProgram,
    PilotStageExecution,
    TelegramConnection,
    User,
    utcnow,
)
from app.security import hash_password
from app.services.delivery import DeliveryService
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)


def _login_client(app, email: str, password: str) -> TestClient:
    """Реализовать внутренний этап login client step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return client


def _create_second_tenant(auth_client: TestClient) -> tuple[TestClient, str]:
    """Реализовать внутренний этап create second tenant step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    email = "portable-owner@example.com"
    password = "PortableOwner_123!"
    with auth_client.app.state.session_factory() as db:
        organization = Organization(
            name="Portable Tenant",
            slug="portable-tenant",
            timezone_name="Europe/Amsterdam",
        )
        db.add(organization)
        db.flush()
        user = User(
            organization_id=organization.id,
            email=email,
            display_name="Portable Owner",
            password_hash=hash_password(password, auth_client.app.state.settings),
            role=UserRole.OWNER,
            is_active=True,
            must_change_password=False,
        )
        db.add(user)
        db.commit()
        organization_id = organization.id
    return _login_client(auth_client.app, email, password), organization_id


def _run_campaign_once(auth_client: TestClient, campaign_id: str) -> str:
    """Реализовать внутренний этап run campaign once step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    run_now = auth_client.post(
        f"/api/v1/campaigns/{campaign_id}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run_now.status_code == 200, run_now.text
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=now) == 1
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="commissioning-portability-test",
    )
    delivery.heartbeat()
    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    runs = auth_client.get(f"/api/v1/campaigns/{campaign_id}/runs")
    assert runs.status_code == 200, runs.text
    return runs.json()[0]["id"]


def _repack_bundle(archive_bytes: bytes, mutate) -> bytes:
    """Реализовать внутренний этап repack bundle step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    document = json.loads(files["bundle.json"])
    manifest = json.loads(files["manifest.json"])
    mutate(document, manifest, files)
    files["bundle.json"] = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest["files"]["bundle.json"] = (
        __import__("hashlib").sha256(files["bundle.json"]).hexdigest()
    )
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _create_ready_local_campaign(client: TestClient) -> tuple[dict, dict, dict, dict]:
    """Реализовать внутренний этап create ready local campaign step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    connection = create_connection(client)
    destination = create_destination(client, connection["id"])
    template = create_template(client)
    campaign = create_campaign(client, connection["id"], template["id"], destination["id"])
    approve_campaign(client, campaign["id"])
    return connection, destination, template, campaign


def _prepare_local_pilot_evidence(
    client: TestClient,
) -> tuple[dict, dict, str]:
    """Реализовать внутренний этап prepare local pilot evidence step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    _connection, _destination, _template, campaign = _create_ready_local_campaign(client)
    commissioning = client.post("/api/v1/commissioning/run", headers=csrf_headers(client))
    assert commissioning.status_code == 201, commissioning.text
    readiness = client.post(
        f"/api/v1/pilot/readiness/{campaign['id']}",
        headers=csrf_headers(client),
    )
    assert readiness.status_code == 201, readiness.text
    created = client.post(
        "/api/v1/pilot/programs",
        headers=csrf_headers(client),
        json={
            "name": "Проверка неизменяемых доказательств",
            "campaign_id": campaign["id"],
            "stage_sizes": [1],
            "require_distinct_signoff": True,
        },
    )
    assert created.status_code == 201, created.text
    program = created.json()
    stage = program["stages"][0]
    started = client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{stage['id']}/start",
        headers=csrf_headers(client),
    )
    assert started.status_code == 200, started.text
    run_id = _run_campaign_once(client, campaign["id"])
    attached = client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{stage['id']}/attach-run",
        headers=csrf_headers(client),
        json={"campaign_run_id": run_id, "note": "Финальный результат запуска"},
    )
    assert attached.status_code == 200, attached.text
    return program, stage, run_id


def test_commissioning_is_persisted_and_safe_for_local_fake_mode(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий commissioning is persisted and safe for local fake mode. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    response = auth_client.post("/api/v1/commissioning/run", headers=csrf_headers(auth_client))
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["status"] == "warning"
    assert len(report["fingerprint"]) == 64
    assert report["summary"]["product_version"] == auth_client.app.state.settings.version
    assert any(item["code"] == "storage_roundtrip" for item in report["checks"])
    assert any(item["code"] == "telegram_runtime_mode" for item in report["checks"])
    assert any(item["code"] == "operational_slo" for item in report["checks"])
    assert any(item["code"] == "continuity_assurance" for item in report["checks"])
    assert not list(
        (auth_client.app.state.settings.storage_path / "commissioning").rglob("*.probe")
    )

    latest = auth_client.get("/api/v1/commissioning/latest")
    assert latest.status_code == 200, latest.text
    assert latest.json()["id"] == report["id"]


def test_local_pilot_stage_captures_evidence_and_advances_sequentially(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий local pilot stage captures evidence and advances sequentially. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = _create_ready_local_campaign(auth_client)

    commissioning = auth_client.post("/api/v1/commissioning/run", headers=csrf_headers(auth_client))
    assert commissioning.status_code == 201, commissioning.text
    assert commissioning.json()["status"] != "blocked"

    readiness = auth_client.post(
        f"/api/v1/pilot/readiness/{campaign['id']}",
        headers=csrf_headers(auth_client),
    )
    assert readiness.status_code == 201, readiness.text
    assert readiness.json()["status"] != "blocked"

    created = auth_client.post(
        "/api/v1/pilot/programs",
        headers=csrf_headers(auth_client),
        json={
            "name": "Контролируемый ввод вакансий",
            "campaign_id": campaign["id"],
            "stage_sizes": [1, 5, 20],
            "require_distinct_signoff": True,
            "notes": "Сначала локальная проверка без Telegram-запросов",
        },
    )
    assert created.status_code == 201, created.text
    program = created.json()
    assert program["status"] == "draft"
    assert [stage["target_destination_count"] for stage in program["stages"]] == [
        1,
        1,
        5,
        20,
    ]
    local_stage = program["stages"][0]

    premature = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{program['stages'][1]['id']}/start",
        headers=csrf_headers(auth_client),
    )
    assert premature.status_code == 409
    assert "последовательно" in premature.text.lower()

    started = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{local_stage['id']}/start",
        headers=csrf_headers(auth_client),
    )
    assert started.status_code == 200, started.text
    assert started.json()["stages"][0]["status"] == "running"

    run_now = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run_now.status_code == 200, run_now.text

    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=now) == 1
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="pilot-program-test",
    )
    delivery.heartbeat()
    assert delivery.process_next(now=now + timedelta(seconds=1)) is True

    runs = auth_client.get(f"/api/v1/campaigns/{campaign['id']}/runs")
    assert runs.status_code == 200, runs.text
    run_id = runs.json()[0]["id"]

    attached = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{local_stage['id']}/attach-run",
        headers=csrf_headers(auth_client),
        json={"campaign_run_id": run_id, "note": "Локальная доставка проверена"},
    )
    assert attached.status_code == 200, attached.text
    attached_stage = attached.json()["stages"][0]
    assert attached_stage["status"] == "awaiting_signoff"
    assert len(attached_stage["evidence_sha256"]) == 64
    assert attached_stage["evidence"]["campaign_run"]["status_counts"]["sent"] == 1
    assert attached_stage["evidence"]["blockers"] == []

    signed = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{local_stage['id']}/signoff",
        headers=csrf_headers(auth_client),
        json={
            "decision": "passed",
            "note": "Локальный этап проверен, дубликатов и ошибок нет",
        },
    )
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["status"] == "active"
    assert body["current_stage_order"] == 1
    assert body["stages"][0]["status"] == "passed"

    blocked_live = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{body['stages'][1]['id']}/start",
        headers=csrf_headers(auth_client),
    )
    assert blocked_live.status_code == 409
    assert "telegram_fake_mode" in blocked_live.text.lower()

    report = auth_client.get(f"/api/v1/pilot/programs/{program['id']}/acceptance-report")
    assert report.status_code == 200, report.text
    wrapper = report.json()
    assert wrapper["manifest"]["payload_sha256"] == report.headers["x-content-sha256"]
    assert wrapper["payload"]["stages"][0]["evidence_sha256"] == attached_stage["evidence_sha256"]


def test_configuration_export_preview_and_fail_safe_import(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration export preview and fail safe import. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection, destination, template, campaign = _create_ready_local_campaign(auth_client)

    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    )
    assert exported.status_code == 201, exported.text
    export_row = exported.json()
    assert export_row["kind"] == "export"
    assert export_row["manifest"]["contains_secrets"] is False
    assert export_row["manifest"]["credential_fields_included"] is False
    assert export_row["manifest"]["free_text_review_required"] is True

    download = auth_client.get(f"/api/v1/configuration-bundles/{export_row['id']}/download")
    assert download.status_code == 200, download.text
    archive_bytes = download.content
    assert download.headers["x-content-sha256"] == export_row["sha256"]
    assert b"1234567890:abcdefghijklmnopqrstuvwxyz" not in archive_bytes

    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
        document = json.loads(archive.read("bundle.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["contains_secrets"] is False
    exported_connection = document["entities"]["connections"][0]
    assert "credentials" not in json.dumps(exported_connection).lower()
    assert document["safety_contract"]["credential_fields_included"] is False
    assert document["safety_contract"]["free_text_review_required"] is True
    assert document["safety_contract"]["connections_import_as_draft"] is True

    files = {"file": ("teleflow-config.zip", archive_bytes, "application/zip")}
    preview = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files=files,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid"] is True
    assert preview.json()["conflicts"]

    imported = auth_client.post(
        "/api/v1/configuration-bundles/import?conflict_mode=rename",
        headers=csrf_headers(auth_client),
        files={"file": ("teleflow-config.zip", archive_bytes, "application/zip")},
    )
    assert imported.status_code == 201, imported.text
    result = imported.json()
    assert result["bundle"]["kind"] == "import"
    assert result["created"]["connections"] >= 1
    assert result["created"]["destinations"] >= 1
    assert result["created"]["campaigns"] >= 1

    with auth_client.app.state.session_factory() as db:
        imported_bundle = db.get(ConfigurationBundle, result["bundle"]["id"])
        assert imported_bundle is not None
        assert imported_bundle.kind == ConfigurationBundleKind.IMPORT

        imported_connections = list(
            db.scalars(
                select(TelegramConnection).where(
                    TelegramConnection.organization_id == connection["organization_id"],
                    TelegramConnection.id != connection["id"],
                )
            ).all()
        )
        assert imported_connections
        imported_connection = imported_connections[-1]
        assert imported_connection.status == ConnectionStatus.DRAFT
        assert imported_connection.credentials_enc is None
        assert imported_connection.require_manual_approval is True

        imported_destinations = list(
            db.scalars(
                select(Destination).where(Destination.connection_id == imported_connection.id)
            ).all()
        )
        assert imported_destinations
        assert all(item.enabled is False for item in imported_destinations)
        assert all(item.validated is False for item in imported_destinations)
        assert all(
            item.permission_status == PermissionStatus.UNVERIFIED for item in imported_destinations
        )

        imported_campaigns = list(
            db.scalars(
                select(Campaign).where(Campaign.connection_id == imported_connection.id)
            ).all()
        )
        assert imported_campaigns
        assert all(item.status.value == "draft" for item in imported_campaigns)
        assert all(item.approved_at is None for item in imported_campaigns)


def test_configuration_bundle_rejects_path_traversal(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle rejects path traversal. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.txt", b"forbidden")
        archive.writestr("bundle.json", b"{}")
        archive.writestr("manifest.json", b"{}")
    response = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("malicious.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 422
    assert "небезопас" in response.text.lower()


def test_pilot_program_can_be_cancelled_without_reviving_stages(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий pilot program can be cancelled without reviving stages. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = _create_ready_local_campaign(auth_client)
    created = auth_client.post(
        "/api/v1/pilot/programs",
        headers=csrf_headers(auth_client),
        json={
            "name": "Отменяемый пилот",
            "campaign_id": campaign["id"],
            "stage_sizes": [1, 5],
            "require_distinct_signoff": False,
        },
    )
    assert created.status_code == 201, created.text
    program_id = created.json()["id"]
    cancelled = auth_client.post(
        f"/api/v1/pilot/programs/{program_id}/cancel",
        headers=csrf_headers(auth_client),
    )
    assert cancelled.status_code == 200, cancelled.text
    refreshed = auth_client.get(f"/api/v1/pilot/programs/{program_id}")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == PilotProgramStatus.CANCELLED.value
    assert all(
        stage["status"] == PilotStageStatus.SKIPPED.value for stage in refreshed.json()["stages"]
    )


def test_pilot_rejects_run_created_before_stage_start(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий pilot rejects run created before stage start. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = _create_ready_local_campaign(auth_client)
    commissioning = auth_client.post("/api/v1/commissioning/run", headers=csrf_headers(auth_client))
    assert commissioning.status_code == 201
    readiness = auth_client.post(
        f"/api/v1/pilot/readiness/{campaign['id']}",
        headers=csrf_headers(auth_client),
    )
    assert readiness.status_code == 201
    old_run_id = _run_campaign_once(auth_client, campaign["id"])

    created = auth_client.post(
        "/api/v1/pilot/programs",
        headers=csrf_headers(auth_client),
        json={
            "name": "Проверка свежести доказательств",
            "campaign_id": campaign["id"],
            "stage_sizes": [1],
            "require_distinct_signoff": True,
        },
    )
    assert created.status_code == 201, created.text
    program = created.json()
    stage = program["stages"][0]
    started = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{stage['id']}/start",
        headers=csrf_headers(auth_client),
    )
    assert started.status_code == 200, started.text
    attach = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{stage['id']}/attach-run",
        headers=csrf_headers(auth_client),
        json={"campaign_run_id": old_run_id, "note": "Старый запуск"},
    )
    assert attach.status_code == 409
    assert "до начала" in attach.text.lower()


def test_live_stage_requires_distinct_admin_signoff(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий live stage requires distinct admin signoff. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = _create_ready_local_campaign(auth_client)
    created = auth_client.post(
        "/api/v1/pilot/programs",
        headers=csrf_headers(auth_client),
        json={
            "name": "Четыре глаза для live",
            "campaign_id": campaign["id"],
            "stage_sizes": [1],
            "require_distinct_signoff": True,
        },
    )
    assert created.status_code == 201, created.text
    program_id = created.json()["id"]

    admin_created = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "pilot-admin@example.com",
            "display_name": "Pilot Admin",
            "password": "PilotAdminPassword_123!",
            "role": "admin",
        },
    )
    assert admin_created.status_code == 201, admin_created.text

    with auth_client.app.state.session_factory() as db:
        owner = db.scalar(select(User).where(User.email == "owner@example.com"))
        program = db.get(PilotProgram, program_id)
        assert owner is not None and program is not None
        live_stage = db.scalar(
            select(PilotStageExecution).where(
                PilotStageExecution.program_id == program_id,
                PilotStageExecution.requires_live_telegram.is_(True),
            )
        )
        assert live_stage is not None
        program.current_stage_order = live_stage.stage_order
        program.status = PilotProgramStatus.ACTIVE
        live_stage.status = PilotStageStatus.AWAITING_SIGNOFF
        live_stage.started_by_id = owner.id
        live_stage.started_at = utcnow()
        evidence = {
            "program_id": program.id,
            "stage_id": live_stage.id,
            "campaign_id": program.campaign_id,
            "campaign_run": {"id": None},
            "target_destination_count": live_stage.target_destination_count,
            "requires_live_telegram": live_stage.requires_live_telegram,
            "blockers": [],
        }
        canonical = json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        live_stage.evidence = evidence
        live_stage.evidence_sha256 = hashlib.sha256(canonical).hexdigest()
        db.commit()
        stage_id = live_stage.id

    same_user = auth_client.post(
        f"/api/v1/pilot/programs/{program_id}/stages/{stage_id}/signoff",
        headers=csrf_headers(auth_client),
        json={"decision": "passed", "note": "Пытаюсь принять собственный live-этап"},
    )
    assert same_user.status_code == 409
    assert "другой" in same_user.text.lower()

    admin = _login_client(auth_client.app, "pilot-admin@example.com", "PilotAdminPassword_123!")
    try:
        accepted = admin.post(
            f"/api/v1/pilot/programs/{program_id}/stages/{stage_id}/signoff",
            headers=csrf_headers(admin),
            json={"decision": "passed", "note": "Независимая проверка выполнена"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == PilotProgramStatus.COMPLETED.value
    finally:
        admin.close()


def test_configuration_bundle_rejects_secret_fields_even_with_valid_manifest(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle rejects secret fields even with valid manifest. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _create_ready_local_campaign(auth_client)
    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    )
    assert exported.status_code == 201, exported.text
    download = auth_client.get(f"/api/v1/configuration-bundles/{exported.json()['id']}/download")
    assert download.status_code == 200

    malicious = _repack_bundle(
        download.content,
        lambda document, _manifest, _files: document["entities"]["connections"][0].update(
            {"bot_token": "1234567890:abcdefghijklmnopqrstuvwxyz"}
        ),
    )
    response = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("secret.zip", malicious, "application/zip")},
    )
    assert response.status_code == 422
    assert "секрет" in response.text.lower()


def test_configuration_bundle_with_media_roundtrips_to_another_tenant(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle with media roundtrips to another tenant. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    upload = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("vacancy.txt", b"approved vacancy media", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = auth_client.post(
        "/api/v1/templates",
        headers=csrf_headers(auth_client),
        json={
            "name": "Вакансия с вложением",
            "body": "Разрешённая публикация",
            "media_asset_id": upload.json()["id"],
        },
    )
    assert template.status_code == 201, template.text
    campaign = create_campaign(
        auth_client, connection["id"], template.json()["id"], destination["id"]
    )
    approve_campaign(auth_client, campaign["id"])

    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": True},
    )
    assert exported.status_code == 201, exported.text
    download = auth_client.get(f"/api/v1/configuration-bundles/{exported.json()['id']}/download")
    assert download.status_code == 200

    tenant_client, tenant_id = _create_second_tenant(auth_client)
    try:
        hidden = tenant_client.get(
            f"/api/v1/configuration-bundles/{exported.json()['id']}/download"
        )
        assert hidden.status_code == 404
        imported = tenant_client.post(
            "/api/v1/configuration-bundles/import?conflict_mode=rename",
            headers=csrf_headers(tenant_client),
            files={
                "file": (
                    "portable-with-media.zip",
                    download.content,
                    "application/zip",
                )
            },
        )
        assert imported.status_code == 201, imported.text
        assert imported.json()["created"]["media"] == 1
        with auth_client.app.state.session_factory() as db:
            asset = db.scalar(select(MediaAsset).where(MediaAsset.organization_id == tenant_id))
            assert asset is not None
            assert asset.sha256 == upload.json()["sha256"]
            assert (
                auth_client.app.state.storage.read_bytes(asset.storage_key)
                == b"approved vacancy media"
            )
    finally:
        tenant_client.close()


def test_commissioning_and_portability_mutations_require_admin_role(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий commissioning and portability mutations require admin role. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    )
    assert exported.status_code == 201, exported.text
    bundle_id = exported.json()["id"]
    for role in ("operator", "viewer"):
        email = f"{role}-portable@example.com"
        password = f"{role.title()}Portable_123!"
        created = auth_client.post(
            "/api/v1/users",
            headers=csrf_headers(auth_client),
            json={
                "email": email,
                "display_name": role.title(),
                "password": password,
                "role": role,
            },
        )
        assert created.status_code == 201, created.text
        client = _login_client(auth_client.app, email, password)
        try:
            assert client.get("/api/v1/commissioning").status_code == 200
            assert client.get("/api/v1/pilot/programs").status_code == 200
            assert client.get("/api/v1/configuration-bundles").status_code == 200
            assert (
                client.get(f"/api/v1/configuration-bundles/{bundle_id}/download").status_code == 403
            )
            assert (
                client.delete(
                    f"/api/v1/configuration-bundles/{bundle_id}",
                    headers=csrf_headers(client),
                ).status_code
                == 403
            )
            assert (
                client.post("/api/v1/commissioning/run", headers=csrf_headers(client)).status_code
                == 403
            )
            assert (
                client.post(
                    "/api/v1/configuration-bundles/export",
                    headers=csrf_headers(client),
                    json={"include_media": False},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/api/v1/pilot/programs",
                    headers=csrf_headers(client),
                    json={
                        "name": "Недоступный пилот",
                        "campaign_id": "00000000-0000-0000-0000-000000000000",
                        "stage_sizes": [1],
                        "require_distinct_signoff": True,
                    },
                ).status_code
                == 403
            )
        finally:
            client.close()


def test_configuration_bundle_rejects_duplicate_paths(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle rejects duplicate paths. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("bundle.json", b"{}")
        archive.writestr("bundle.json", b"{}")
        archive.writestr("manifest.json", b"{}")
    response = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("duplicate.zip", duplicate.getvalue(), "application/zip")},
    )
    assert response.status_code == 422
    assert "повторяющ" in response.text.lower()


def test_configuration_bundle_invalid_values_return_controlled_error(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle invalid values return controlled error. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _create_ready_local_campaign(auth_client)
    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    )
    archive_bytes = auth_client.get(
        f"/api/v1/configuration-bundles/{exported.json()['id']}/download"
    ).content
    invalid = _repack_bundle(
        archive_bytes,
        lambda document, _manifest, _files: document["entities"]["connections"][0].update(
            {"kind": "unsupported_transport"}
        ),
    )
    response = auth_client.post(
        "/api/v1/configuration-bundles/import?conflict_mode=rename",
        headers=csrf_headers(auth_client),
        files={"file": ("invalid.zip", invalid, "application/zip")},
    )
    assert response.status_code == 400
    assert "недопустим" in response.text.lower()


def test_portability_downloads_are_recorded_in_audit(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий portability downloads are recorded in audit. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = _create_ready_local_campaign(auth_client)
    bundle = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    ).json()
    assert (
        auth_client.get(f"/api/v1/configuration-bundles/{bundle['id']}/download").status_code == 200
    )

    program = auth_client.post(
        "/api/v1/pilot/programs",
        headers=csrf_headers(auth_client),
        json={
            "name": "Аудируемый пилот",
            "campaign_id": campaign["id"],
            "stage_sizes": [1],
            "require_distinct_signoff": False,
        },
    ).json()
    assert (
        auth_client.get(f"/api/v1/pilot/programs/{program['id']}/acceptance-report").status_code
        == 200
    )

    with auth_client.app.state.session_factory() as db:
        actions = set(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.organization_id == bundle["organization_id"],
                    AuditLog.action.in_(
                        [
                            "configuration_bundle.downloaded",
                            "pilot_program.acceptance_report_downloaded",
                        ]
                    ),
                )
            ).all()
        )
    assert actions == {
        "configuration_bundle.downloaded",
        "pilot_program.acceptance_report_downloaded",
    }


def test_campaign_run_evidence_cannot_be_reused_across_pilot_stages(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий campaign run evidence cannot be reused across pilot stages. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = _create_ready_local_campaign(auth_client)
    assert (
        auth_client.post("/api/v1/commissioning/run", headers=csrf_headers(auth_client)).status_code
        == 201
    )
    assert (
        auth_client.post(
            f"/api/v1/pilot/readiness/{campaign['id']}",
            headers=csrf_headers(auth_client),
        ).status_code
        == 201
    )
    created = auth_client.post(
        "/api/v1/pilot/programs",
        headers=csrf_headers(auth_client),
        json={
            "name": "Запрет повторного доказательства",
            "campaign_id": campaign["id"],
            "stage_sizes": [1],
            "require_distinct_signoff": False,
        },
    )
    assert created.status_code == 201, created.text
    program = created.json()
    local_stage = program["stages"][0]
    assert (
        auth_client.post(
            f"/api/v1/pilot/programs/{program['id']}/stages/{local_stage['id']}/start",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )
    run_id = _run_campaign_once(auth_client, campaign["id"])
    attached = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{local_stage['id']}/attach-run",
        headers=csrf_headers(auth_client),
        json={"campaign_run_id": run_id, "note": "Первое доказательство"},
    )
    assert attached.status_code == 200, attached.text

    with auth_client.app.state.session_factory() as db:
        run = db.get(CampaignRun, run_id)
        pilot = db.get(PilotProgram, program["id"])
        live_stage = db.scalar(
            select(PilotStageExecution).where(
                PilotStageExecution.program_id == program["id"],
                PilotStageExecution.requires_live_telegram.is_(True),
            )
        )
        assert run is not None and pilot is not None and live_stage is not None
        live_stage.status = PilotStageStatus.RUNNING
        live_stage.started_at = run.created_at - timedelta(seconds=1)
        pilot.current_stage_order = live_stage.stage_order
        pilot.status = PilotProgramStatus.ACTIVE
        db.commit()
        live_stage_id = live_stage.id

    reused = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{live_stage_id}/attach-run",
        headers=csrf_headers(auth_client),
        json={"campaign_run_id": run_id, "note": "Повторное доказательство"},
    )
    assert reused.status_code == 409
    assert "уже используется" in reused.text.lower()


def test_configuration_bundle_rejects_duplicate_zip_entries(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle rejects duplicate zip entries. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _create_ready_local_campaign(auth_client)
    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    )
    assert exported.status_code == 201, exported.text
    downloaded = auth_client.get(f"/api/v1/configuration-bundles/{exported.json()['id']}/download")
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content), "r") as source:
        entries = [(info.filename, source.read(info)) for info in source.infolist()]
    duplicate = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries:
                archive.writestr(name, payload)
            archive.writestr("bundle.json", dict(entries)["bundle.json"])
    response = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("duplicate.zip", duplicate.getvalue(), "application/zip")},
    )
    assert response.status_code == 422
    assert "повторяющиеся" in response.text.lower()


def test_pilot_evidence_is_immutable_and_verified_before_signoff(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий pilot evidence is immutable and verified before signoff. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    program, stage, run_id = _prepare_local_pilot_evidence(auth_client)

    repeated = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{stage['id']}/attach-run",
        headers=csrf_headers(auth_client),
        json={"campaign_run_id": run_id, "note": "Попытка заменить доказательство"},
    )
    assert repeated.status_code == 409
    assert "неизменяем" in repeated.text.lower()

    with auth_client.app.state.session_factory() as db:
        stored = db.get(PilotStageExecution, stage["id"])
        assert stored is not None
        tampered = dict(stored.evidence)
        tampered["operator_note"] = "подмена после фиксации"
        stored.evidence = tampered
        db.commit()

    rejected = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{stage['id']}/signoff",
        headers=csrf_headers(auth_client),
        json={"decision": "passed", "note": "Пытаюсь принять изменённые данные"},
    )
    assert rejected.status_code == 409
    assert "целостност" in rejected.text.lower()

    report = auth_client.get(f"/api/v1/pilot/programs/{program['id']}/acceptance-report")
    assert report.status_code == 200, report.text
    payload = report.json()["payload"]
    assert payload["evidence_integrity"]["valid"] is False
    assert payload["evidence_integrity"]["issues"] == [
        {"stage_id": stage["id"], "reason": "hash_mismatch"}
    ]


def test_rejected_pilot_stage_restarts_with_clean_evidence(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий rejected pilot stage restarts with clean evidence. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    program, stage, _run_id = _prepare_local_pilot_evidence(auth_client)

    failed = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{stage['id']}/signoff",
        headers=csrf_headers(auth_client),
        json={"decision": "failed", "note": "Нужен повтор после корректировки"},
    )
    assert failed.status_code == 200, failed.text
    failed_stage = failed.json()["stages"][0]
    assert failed_stage["status"] == PilotStageStatus.FAILED.value
    assert failed_stage["evidence"]

    restarted = auth_client.post(
        f"/api/v1/pilot/programs/{program['id']}/stages/{stage['id']}/start",
        headers=csrf_headers(auth_client),
    )
    assert restarted.status_code == 200, restarted.text
    restarted_stage = restarted.json()["stages"][0]
    assert restarted_stage["status"] == PilotStageStatus.RUNNING.value
    assert restarted_stage["campaign_run_id"] is None
    assert restarted_stage["evidence"] == {}
    assert restarted_stage["evidence_sha256"] is None
    assert restarted_stage["signed_off_by_id"] is None
    assert restarted_stage["signed_off_at"] is None
    assert restarted_stage["signoff_note"] is None


def test_configuration_bundle_requires_explicit_safety_contract(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle requires explicit safety contract. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _create_ready_local_campaign(auth_client)
    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    )
    assert exported.status_code == 201, exported.text
    download = auth_client.get(f"/api/v1/configuration-bundles/{exported.json()['id']}/download")
    assert download.status_code == 200, download.text

    no_text_review = _repack_bundle(
        download.content,
        lambda document, manifest, _files: (
            document["safety_contract"].update({"free_text_review_required": False}),
            manifest.update({"free_text_review_required": False}),
        ),
    )
    rejected_review = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("unsafe-review.zip", no_text_review, "application/zip")},
    )
    assert rejected_review.status_code == 422
    assert "свободного текста" in rejected_review.text.lower()

    credentials_claimed = _repack_bundle(
        download.content,
        lambda document, manifest, _files: (
            document["safety_contract"].update({"credential_fields_included": True}),
            manifest.update({"credential_fields_included": True}),
        ),
    )
    rejected_credentials = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={
            "file": (
                "unsafe-credentials.zip",
                credentials_claimed,
                "application/zip",
            )
        },
    )
    assert rejected_credentials.status_code == 422
    assert "credential" in rejected_credentials.text.lower()


def test_pilot_program_rejects_non_certified_stage_sizes(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий pilot program rejects non certified stage sizes. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _connection, _destination, _template, campaign = _create_ready_local_campaign(auth_client)
    response = auth_client.post(
        "/api/v1/pilot/programs",
        headers=csrf_headers(auth_client),
        json={
            "name": "Недопустимый произвольный масштаб",
            "campaign_id": campaign["id"],
            "stage_sizes": [1, 10],
            "require_distinct_signoff": True,
        },
    )
    assert response.status_code == 422, response.text
    assert "1, 5, 20, 50" in response.text


def test_configuration_bundle_can_be_deleted_with_storage_cleanup(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle can be deleted with storage cleanup. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    )
    assert exported.status_code == 201, exported.text
    bundle = exported.json()
    with auth_client.app.state.session_factory() as db:
        stored = db.get(ConfigurationBundle, bundle["id"])
        assert stored is not None
        storage_key = stored.storage_key
    assert auth_client.app.state.storage.exists(storage_key)

    deleted = auth_client.delete(
        f"/api/v1/configuration-bundles/{bundle['id']}",
        headers=csrf_headers(auth_client),
    )
    assert deleted.status_code == 200, deleted.text
    assert not auth_client.app.state.storage.exists(storage_key)
    assert (
        auth_client.get(f"/api/v1/configuration-bundles/{bundle['id']}/download").status_code == 404
    )

    with auth_client.app.state.session_factory() as db:
        assert db.get(ConfigurationBundle, bundle["id"]) is None
        actions = set(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.organization_id.is_not(None),
                    AuditLog.entity_id == bundle["id"],
                )
            ).all()
        )
    assert "configuration_bundle.deleted" in actions
