from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def build_test_settings(
    database_path: Path, storage_path: Path, *, create_schema: bool
) -> Settings:
    """Создать изолированные настройки приложения для тестовой SQLite-базы."""

    return Settings(
        _env_file=None,
        environment="test",
        debug=True,
        database_url=f"sqlite:///{database_path}",
        auto_create_schema=create_schema,
        master_key="test-master-key-with-sufficient-entropy-0123456789",
        jwt_secret="test-jwt-secret-with-more-than-thirty-two-characters-0123456789",
        bootstrap_admin_email="owner@example.com",
        bootstrap_admin_password="OwnerPassword_123!",
        bootstrap_admin_name="Test Owner",
        public_base_url="http://testserver",
        cors_origins="http://testserver",
        allowed_hosts="testserver,localhost,127.0.0.1",
        telegram_fake_mode=True,
        storage_path=storage_path,
        worker_poll_seconds=0.01,
        user_hard_min_interval_seconds=60,
        user_default_interval_seconds=90,
        bot_default_interval_seconds=1,
        default_destination_cooldown_minutes=60,
        password_hash_time_cost=1,
        password_hash_memory_cost_kib=8192,
        password_hash_parallelism=1,
    )


@pytest.fixture(scope="session")
def initialized_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Один раз создать эталонную схему с bootstrap-владельцем для быстрых копий."""

    root = tmp_path_factory.mktemp("teleflow-template")
    database_path = root / "teleflow-template.db"
    template_settings = build_test_settings(database_path, root / "storage", create_schema=True)
    with TestClient(create_app(template_settings)):
        pass
    return database_path


@pytest.fixture
def settings(tmp_path: Path, initialized_database: Path) -> Settings:
    """Клонировать эталонную БД, сохраняя полную изоляцию отдельного теста."""

    database_path = tmp_path / "teleflow-test.db"
    shutil.copyfile(initialized_database, database_path)
    # Сохраняем production-код старта без подмены: некоторые safety-gate учитывают этот флаг.
    # На уже готовой копии create_all выполняет только дешёвую проверку существующей схемы.
    return build_test_settings(database_path, tmp_path / "storage", create_schema=True)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Запустить приложение и гарантированно закрыть его ресурсы после теста."""
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """Авторизовать TestClient под bootstrap-владельцем."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "OwnerPassword_123!"},
    )
    assert response.status_code == 200, response.text
    return client


def csrf_headers(client: TestClient) -> dict[str, str]:
    """Вернуть CSRF-заголовок из cookie текущего клиента."""
    token = client.cookies.get("teleflow_csrf")
    assert token
    return {"X-CSRF-Token": token}


@pytest.fixture
def csrf(auth_client: TestClient) -> dict[str, str]:
    """Предоставить CSRF-заголовок уже авторизованного клиента."""
    return csrf_headers(auth_client)
