from __future__ import annotations

from fastapi.testclient import TestClient


def test_pwa_manifest_and_icons_are_served(client: TestClient) -> None:
    """Проверить сценарий pwa manifest and icons are served. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    manifest_response = client.get("/manifest.webmanifest")
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["name"] == "TeleFlow Platform"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/#/dashboard"
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}

    for path in ("/icons/icon-192.png", "/icons/icon-512.png", "/icons/icon-maskable-512.png"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_service_worker_never_caches_sensitive_routes(client: TestClient) -> None:
    """Проверить сценарий service worker never caches sensitive routes. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    response = client.get("/sw.js")
    assert response.status_code == 200
    source = response.text
    assert 'pathname.startsWith("/api/")' in source
    assert 'pathname.startsWith("/hooks/")' in source
    assert 'pathname === "/metrics"' in source
    assert "isSensitivePath(url.pathname)" in source
    assert 'caches.match("/offline.html")' in source
    assert "teleflow-shell-v2.5.0" in source


def test_offline_page_contains_no_user_data_and_explains_policy(client: TestClient) -> None:
    """Проверить сценарий offline page contains no user data and explains policy. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    response = client.get("/offline.html")
    assert response.status_code == 200
    assert "не хранит API-ответы" in response.text
    assert "данные авторизации" in response.text
    assert "teleflow_csrf" not in response.text
    assert "localStorage" not in response.text


def test_index_registers_manifest_and_service_worker(client: TestClient) -> None:
    """Проверить сценарий index registers manifest and service worker. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    index = client.get("/")
    assert index.status_code == 200
    assert 'rel="manifest" href="/manifest.webmanifest"' in index.text
    assert 'rel="apple-touch-icon" href="/icons/icon-192.png"' in index.text

    app_js = client.get("/app.js")
    assert app_js.status_code == 200
    assert 'navigator.serviceWorker.register("/sw.js"' in app_js.text


def test_production_pilot_ui_and_mutations_are_role_gated(client: TestClient) -> None:
    """Проверить сценарий production pilot ui and mutations are role gated. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    app_js = client.get("/app.js")
    assert app_js.status_code == 200
    source = app_js.text
    assert 'pilot: ["Production Pilot"' in source
    assert 'api("/pilot/overview")' in source
    assert 'api("/pilot/stage")' in source
    assert 'api("/pilot/stage/assessments?limit=50")' in source
    assert 'api("/pilot/canaries?limit=50")' in source
    assert 'api("/pilot/support-bundles?limit=50")' in source
    assert 'api("/blackouts")' in source
    assert 'can("owner", "admin") ? api("/pilot/support-bundles?limit=50")' in source
    assert 'const operatorActions = can("owner", "admin", "operator")' in source
    assert 'const validationActions = can("owner", "admin", "operator")' in source
    assert 'can("owner", "admin", "operator") ? button("Preflight"' in source


def test_commissioning_portability_ui_is_present_and_admin_gated(
    client: TestClient,
) -> None:
    """Проверить сценарий commissioning portability ui is present and admin gated. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    response = client.get("/app.js")
    assert response.status_code == 200
    source = response.text
    assert 'commissioning: ["Ввод в эксплуатацию"' in source
    assert 'api("/commissioning?limit=100")' in source
    assert 'api("/pilot/programs")' in source
    assert 'api("/configuration-bundles?limit=100")' in source
    assert 'data-action="commissioning-run"' in source
    assert 'data-action="pilot-program-new"' in source
    assert 'data-action="bundle-export"' in source
    assert 'data-action="bundle-import"' in source
    assert 'button("Удалить", "bundle-delete"' in source
    assert 'can("owner", "admin")' in source


def test_artifact_trust_ui_is_present_and_secrets_are_not_exposed(
    client: TestClient,
) -> None:
    """Проверить сценарий artifact trust ui is present and secrets are not exposed. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    response = client.get("/app.js")
    assert response.status_code == 200
    source = response.text
    assert 'artifactTrust: ["Подписи и доверие"' in source
    assert 'recovery: ["Восстановление"' in source
    assert "async recovery()" in source
    assert 'api("/artifact-signing-keys")' in source
    assert 'api("/artifact-signing-keys/verify-artifact"' in source
    assert 'data-action="artifact-key-generate"' in source
    assert 'data-action="artifact-key-import"' in source
    assert 'data-action="artifact-verify"' in source
    assert "private_key_enc" not in source


def test_supply_chain_ui_is_present_and_sensitive_evidence_is_not_cached(
    client: TestClient,
) -> None:
    """Проверить сценарий supply chain ui is present and sensitive evidence is not cached. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    response = client.get("/app.js")
    assert response.status_code == 200
    source = response.text
    assert 'supplyChain: ["Поставка и зависимости"' in source
    assert "async supplyChain()" in source
    assert 'api("/supply-chain/policy")' in source
    assert 'api("/supply-chain/assessments?limit=200")' in source
    assert 'api("/supply-chain/transparency?limit=500")' in source
    assert 'data-action="supply-policy-edit"' in source
    assert 'data-action="supply-assessment-new"' in source
    assert 'data-action="supply-transparency-verify"' in source
    sw = client.get("/sw.js").text
    assert 'pathname.startsWith("/api/")' in sw


def test_operational_slo_ui_is_present_and_role_gated(client: TestClient) -> None:
    """Проверить сценарий operational slo ui is present and role gated. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    response = client.get("/app.js")
    assert response.status_code == 200
    source = response.text
    assert 'operations: ["Надёжность и инциденты"' in source
    assert "async operations()" in source
    assert 'api("/operations/overview")' in source
    assert 'api("/operations/slo-assessments?limit=100")' in source
    assert 'api("/operations/incidents?limit=200")' in source
    assert 'data-action="operations-assess"' in source
    assert 'data-action="slo-policy-edit"' in source
    assert 'data-action="incident-new"' in source
    assert '"incident-acknowledge"' in source
    assert '"incident-resolve"' in source
    assert '"incident-close"' in source
    assert 'can("owner", "admin")' in source
    assert 'can("owner", "admin", "operator")' in source


def test_execution_fencing_ui_is_present_and_admin_gated(client: TestClient) -> None:
    """Проверить сценарий execution fencing ui is present and admin gated. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    response = client.get("/app.js")
    assert response.status_code == 200
    source = response.text
    assert 'execution: ["Active / Standby"' in source
    assert "async execution()" in source
    assert 'api("/execution/overview")' in source
    assert 'api("/execution/failovers?limit=100")' in source
    assert 'api("/execution/delivery-attempts?limit=100")' in source
    assert 'data-action="execution-failover-new"' in source
    assert '"execution-failover-approve"' in source
    assert '"execution-failover-cancel"' in source
    assert "const phrase = `ПЕРЕКЛЮЧИТЬ НА ${item.target_site_key}`" in source
    assert 'can("owner", "admin")' in source
    assert "private_key_enc" not in source


def test_continuity_assurance_ui_is_present_and_admin_gated(client: TestClient) -> None:
    """Тридцать первый SPA section exposes drills without leaking privileged mutations."""

    response = client.get("/app.js")
    assert response.status_code == 200
    source = response.text
    assert 'continuity: ["Непрерывность"' in source
    assert "async continuity()" in source
    assert 'api("/continuity/overview")' in source
    assert 'api("/continuity/drills?limit=200")' in source
    assert 'data-action="continuity-new"' in source
    assert '"continuity-start"' in source
    assert '"continuity-failback"' in source
    assert '"continuity-signoff"' in source
    assert '"continuity-verify"' in source
    assert 'can("owner", "admin")' in source
    assert "private_key_enc" not in source


def test_capacity_backpressure_ui_is_present_and_admin_gated(client: TestClient) -> None:
    """Проверить the 32nd SPA section exposes capacity evidence without client-side-only gates."""

    response = client.get("/app.js")
    assert response.status_code == 200
    source = response.text
    assert 'capacity: ["Нагрузка и лимиты"' in source
    assert "async capacity()" in source
    assert 'api("/capacity/overview")' in source
    assert 'api("/capacity/assessments?limit=100")' in source
    assert 'data-action="capacity-policy-edit"' in source
    assert 'data-action="capacity-assess"' in source
    assert 'form.id === "capacity-policy-form"' in source
    assert 'can("owner", "admin")' in source
    assert "private_key_enc" not in source


def test_generated_form_labels_are_programmatically_associated(client: TestClient) -> None:
    """Все динамические формы получают доступные подписи после отрисовки."""

    response = client.get("/app.js")
    assert response.status_code == 200
    source = response.text
    assert "function ensureAccessibleFormLabels(container)" in source
    assert "ensureAccessibleFormLabels(modalRoot);" in source
    assert "ensureAccessibleFormLabels(page);" in source
    assert "label.htmlFor = control.id;" in source
