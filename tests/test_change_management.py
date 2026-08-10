from datetime import UTC

from fastapi.testclient import TestClient

from tests.conftest import csrf_headers


def login(client: TestClient, email: str, password: str) -> None:
    """Выполнить операцию login. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


def test_upgrade_change_requires_independent_approval_and_maintenance(auth_client: TestClient):
    """Проверить сценарий upgrade change requires independent approval and maintenance. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    r = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "change-admin@example.com",
            "display_name": "Change Admin",
            "password": "ChangeAdmin_123!",
            "role": "admin",
        },
    )
    assert r.status_code == 201, r.text
    created = auth_client.post(
        "/api/v1/changes",
        headers=csrf_headers(auth_client),
        json={
            "title": "Обновление до 2.1",
            "change_type": "upgrade",
            "current_version": "2.5.0",
            "target_version": "2.5.0",
            "reason": "Проверка управляемого обновления",
            "risk_summary": "Возможна временная недоступность",
            "rollback_plan": "Вернуть предыдущий образ и выполнить downgrade миграции",
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    self_approve = auth_client.post(
        f"/api/v1/changes/{cid}/approve", headers=csrf_headers(auth_client)
    )
    assert self_approve.status_code == 409
    login(auth_client, "change-admin@example.com", "ChangeAdmin_123!")
    approved = auth_client.post(f"/api/v1/changes/{cid}/approve", headers=csrf_headers(auth_client))
    assert approved.status_code == 200, approved.text
    blocked = auth_client.post(f"/api/v1/changes/{cid}/start", headers=csrf_headers(auth_client))
    assert blocked.status_code == 409
    maintenance = auth_client.post(
        "/api/v1/changes/maintenance/start",
        headers=csrf_headers(auth_client),
        json={"reason": "Плановое обновление платформы"},
    )
    assert maintenance.status_code == 200 and maintenance.json()["maintenance_mode"] is True
    started = auth_client.post(f"/api/v1/changes/{cid}/start", headers=csrf_headers(auth_client))
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"
    stop = auth_client.post(
        "/api/v1/changes/maintenance/stop",
        headers=csrf_headers(auth_client),
        json={"note": "слишком рано"},
    )
    assert stop.status_code == 409
    completed = auth_client.post(
        f"/api/v1/changes/{cid}/complete",
        headers=csrf_headers(auth_client),
        json={"note": "Проверки успешны"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    stop = auth_client.post(
        "/api/v1/changes/maintenance/stop",
        headers=csrf_headers(auth_client),
        json={"note": "Обслуживание завершено"},
    )
    assert stop.status_code == 200 and stop.json()["maintenance_mode"] is False


def test_maintenance_is_exposed_in_organization(auth_client: TestClient):
    """Проверить сценарий maintenance is exposed in organization. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    r = auth_client.get("/api/v1/organization")
    assert r.status_code == 200
    assert r.json()["maintenance_mode"] is False


def test_maintenance_blocks_campaign_run_and_scheduler(auth_client: TestClient):
    """Проверить сценарий maintenance blocks campaign run and scheduler. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    from datetime import datetime, timedelta

    from app.services.scheduler import SchedulerService
    from tests.test_api_workflow import (
        approve_campaign,
        create_campaign,
        create_connection,
        create_destination,
        create_template,
    )

    connection = create_connection(auth_client, name="Maintenance Bot")
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    start = auth_client.post(
        "/api/v1/changes/maintenance/start",
        headers=csrf_headers(auth_client),
        json={"reason": "Проверка блокировки публикаций"},
    )
    assert start.status_code == 200
    run = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now", headers=csrf_headers(auth_client)
    )
    assert run.status_code == 409
    assert "проверка блокировки публикаций" in run.text.lower()
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=3)) == 0


def test_upgrade_requires_target_version(auth_client: TestClient):
    """Проверить сценарий upgrade requires target version. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    r = auth_client.post(
        "/api/v1/changes",
        headers=csrf_headers(auth_client),
        json={
            "title": "Некорректное обновление",
            "change_type": "upgrade",
            "reason": "Проверка схемы запроса",
            "risk_summary": "Риск тестовый",
            "rollback_plan": "Откатить изменения",
        },
    )
    assert r.status_code == 422


def test_post_change_version_drift_blocks_completion_and_can_be_failed(auth_client: TestClient):
    """Проверить сценарий post change version drift blocks completion and can be failed. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    create = auth_client.post(
        "/api/v1/changes",
        headers=csrf_headers(auth_client),
        json={
            "title": "Конфигурационный rollout",
            "change_type": "configuration",
            "current_version": "2.5.0",
            "target_version": "9.9.9",
            "reason": "Проверяем обнаружение дрейфа версии",
            "risk_summary": "Неверная версия после deploy",
            "rollback_plan": "Вернуть предыдущую конфигурацию",
        },
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]
    # Owner cannot approve own request, so create an independent admin.
    admin = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "drift-admin@example.com",
            "display_name": "Drift Admin",
            "password": "DriftAdmin_123!",
            "role": "admin",
        },
    )
    assert admin.status_code == 201
    login(auth_client, "drift-admin@example.com", "DriftAdmin_123!")
    assert (
        auth_client.post(
            f"/api/v1/changes/{cid}/approve", headers=csrf_headers(auth_client)
        ).status_code
        == 200
    )
    started = auth_client.post(f"/api/v1/changes/{cid}/start", headers=csrf_headers(auth_client))
    assert started.status_code == 200, started.text
    complete = auth_client.post(
        f"/api/v1/changes/{cid}/complete",
        headers=csrf_headers(auth_client),
        json={"note": "Проверить version drift"},
    )
    assert complete.status_code == 409
    assert "9.9.9" in complete.text
    failed = auth_client.post(
        f"/api/v1/changes/{cid}/fail",
        headers=csrf_headers(auth_client),
        json={"note": "Целевая версия не совпала, требуется rollback"},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    assert failed.json()["result_summary"]["rollback_required"] is True


def test_change_cancel_and_operator_rbac(auth_client: TestClient):
    """Проверить сценарий change cancel and operator rbac. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    created = auth_client.post(
        "/api/v1/changes",
        headers=csrf_headers(auth_client),
        json={
            "title": "Отменяемая настройка",
            "change_type": "configuration",
            "reason": "Проверка отмены изменения",
            "risk_summary": "Низкий риск",
            "rollback_plan": "Изменение ещё не начато",
        },
    )
    assert created.status_code == 201
    cid = created.json()["id"]
    cancelled = auth_client.post(
        f"/api/v1/changes/{cid}/cancel",
        headers=csrf_headers(auth_client),
        json={"note": "Окно перенесено"},
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    operator = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "change-operator@example.com",
            "display_name": "Change Operator",
            "password": "ChangeOperator_123!",
            "role": "operator",
        },
    )
    assert operator.status_code == 201
    login(auth_client, "change-operator@example.com", "ChangeOperator_123!")
    denied = auth_client.post(
        "/api/v1/changes",
        headers=csrf_headers(auth_client),
        json={
            "title": "Запрещённое изменение",
            "change_type": "configuration",
            "reason": "Проверка RBAC доступа",
            "risk_summary": "Не должен создаваться",
            "rollback_plan": "Не требуется",
        },
    )
    assert denied.status_code == 403
    assert auth_client.get("/api/v1/changes").status_code == 200


def test_slo_gate_blocks_change_start_until_current_assessment(
    auth_client: TestClient,
):
    """Проверить сценарий slo gate blocks change start until current assessment. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    from datetime import datetime

    from app.models import WorkerHeartbeat

    policy = auth_client.patch(
        "/api/v1/operations/slo-policy",
        headers=csrf_headers(auth_client),
        json={
            "enabled": True,
            "gate_changes": True,
            "gate_publishing": False,
            "minimum_delivery_sample_size": 1,
        },
    )
    assert policy.status_code == 200, policy.text

    created = auth_client.post(
        "/api/v1/changes",
        headers=csrf_headers(auth_client),
        json={
            "title": "SLO-gated configuration change",
            "change_type": "configuration",
            "current_version": "2.5.0",
            "target_version": "2.5.0",
            "reason": "Проверка обязательной операционной оценки",
            "risk_summary": "Изменение не должно стартовать при нарушенном SLO",
            "rollback_plan": "Отменить изменение без применения",
        },
    )
    assert created.status_code == 201, created.text
    change_id = created.json()["id"]

    admin = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "slo-change-admin@example.com",
            "display_name": "SLO Change Admin",
            "password": "SloChangeAdmin_123!",
            "role": "admin",
        },
    )
    assert admin.status_code == 201, admin.text
    login(auth_client, "slo-change-admin@example.com", "SloChangeAdmin_123!")
    assert (
        auth_client.post(
            f"/api/v1/changes/{change_id}/approve",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )
    assert (
        auth_client.post(
            "/api/v1/changes/maintenance/start",
            headers=csrf_headers(auth_client),
            json={"reason": "SLO-gated maintenance window"},
        ).status_code
        == 200
    )

    blocked = auth_client.post(
        f"/api/v1/changes/{change_id}/start",
        headers=csrf_headers(auth_client),
    )
    assert blocked.status_code == 409
    assert "SLO" in blocked.text

    with auth_client.app.state.session_factory() as db:
        db.add(
            WorkerHeartbeat(
                worker_id="slo-change-worker",
                hostname="test-host",
                pid=1,
                version=auth_client.app.state.settings.version,
                last_seen_at=datetime.now(UTC),
                details={"test": True},
            )
        )
        db.commit()
    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201, assessed.text
    assert assessed.json()["status"] == "warning"

    started = auth_client.post(
        f"/api/v1/changes/{change_id}/start",
        headers=csrf_headers(auth_client),
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"
