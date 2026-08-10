from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.business import hooks_router
from app.api.router import api_router
from app.config import Settings, get_settings
from app.database import create_database_engine, create_session_factory, initialize_database
from app.middleware import (
    CSRFMiddleware,
    IPAllowlistMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.observability import MetricsMiddleware, initialize_observability, metrics_response
from app.services.bootstrap import bootstrap_admin
from app.services.crypto import SecretCipher
from app.services.locks import DistributedLockManager
from app.services.logging import configure_logging
from app.services.storage import StorageService


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создать app. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    settings = settings or get_settings()
    settings.validate_runtime_security()
    configure_logging(settings.log_level, settings.log_format)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    cipher = SecretCipher(settings.master_key)
    storage = StorageService(settings)
    lock_manager = DistributedLockManager(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Выполнить операцию lifespan. Аргументы интерпретируются в контексте модуля, результат
        возвращается вызывающему коду.
        """
        Path(settings.storage_path).mkdir(parents=True, exist_ok=True)
        settings.media_path.mkdir(parents=True, exist_ok=True)
        settings.exports_path.mkdir(parents=True, exist_ok=True)
        if settings.effective_auto_create_schema:
            initialize_database(engine)
        bootstrap_admin(session_factory, settings)
        yield
        lock_manager.close()
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.debug,
        docs_url="/api/docs" if settings.debug else None,
        redoc_url="/api/redoc" if settings.debug else None,
        openapi_url="/api/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.cipher = cipher
    app.state.storage = storage
    app.state.lock_manager = lock_manager

    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(CSRFMiddleware, settings=settings)
    app.add_middleware(IPAllowlistMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware, settings=settings)
    if settings.metrics_enabled:
        app.add_middleware(MetricsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-CSRF-Token",
            "X-Request-ID",
            "X-API-Key",
            "X-Telegram-Bot-Api-Secret-Token",
        ],
    )
    if settings.allowed_host_list:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(hooks_router)
    if settings.metrics_enabled:
        app.add_api_route(
            settings.metrics_path, metrics_response, methods=["GET"], include_in_schema=False
        )
    initialize_observability(app, settings)

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
