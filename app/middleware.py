from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import Settings


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings):
        """Инициализировать RequestContextMiddleware with its explicit dependencies. Сохраняется
        только состояние, необходимое последующим операциям.
        """
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Выполнить операцию dispatch класса RequestContextMiddleware. Аргументы интерпретируются
        в контексте модуля, результат возвращается вызывающему коду.
        """
        request_id = request.headers.get("x-request-id") or secrets.token_hex(12)
        request.state.request_id = request_id
        direct_ip = request.client.host if request.client else None
        forwarded = (
            request.headers.get("x-forwarded-for") if self.settings.trust_proxy_headers else None
        )
        request.state.client_ip = forwarded.split(",", 1)[0].strip() if forwarded else direct_ip
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings):
        """Инициализировать SecurityHeadersMiddleware with its explicit dependencies. Сохраняется
        только состояние, необходимое последующим операциям.
        """
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Выполнить операцию dispatch класса SecurityHeadersMiddleware. Аргументы интерпретируются
        в контексте модуля, результат возвращается вызывающему коду.
        """
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'",
        )
        response.headers.setdefault("Cache-Control", "no-store")
        if self.settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    EXEMPT_PATHS = {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/health/live",
        "/api/v1/health/ready",
    }

    def __init__(self, app: ASGIApp, settings: Settings):
        """Инициализировать CSRFMiddleware with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Выполнить операцию dispatch класса CSRFMiddleware. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if request.method in self.SAFE_METHODS or request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        # Telegram authenticates webhook calls with a high-entropy path token and
        # X-Telegram-Bot-Api-Secret-Token. No browser session is used here.
        if request.url.path.startswith("/hooks/telegram/"):
            return await call_next(request)
        # Bearer and explicit API-key clients are not vulnerable to cookie CSRF.
        if request.headers.get("authorization", "").lower().startswith("bearer "):
            return await call_next(request)
        if request.headers.get("x-api-key"):
            return await call_next(request)
        access_cookie = request.cookies.get(self.settings.access_cookie_name)
        if not access_cookie:
            return await call_next(request)
        cookie_token = request.cookies.get(self.settings.csrf_cookie_name)
        header_token = request.headers.get("x-csrf-token")
        if (
            not cookie_token
            or not header_token
            or not secrets.compare_digest(cookie_token, header_token)
        ):
            return JSONResponse(status_code=403, content={"detail": "CSRF-проверка не пройдена"})
        return await call_next(request)


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings):
        """Инициализировать IPAllowlistMiddleware with its explicit dependencies. Сохраняется
        только состояние, необходимое последующим операциям.
        """
        super().__init__(app)
        self.settings = settings
        self.networks = []
        for value in settings.ip_allowlist_values:
            try:
                self.networks.append(ipaddress.ip_network(value, strict=False))
            except ValueError:
                self.networks.append(ipaddress.ip_network(f"{value}/32", strict=False))

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Выполнить операцию dispatch класса IPAllowlistMiddleware. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if request.url.path.startswith("/hooks/telegram/"):
            return await call_next(request)
        if not self.networks:
            return await call_next(request)
        forwarded = (
            request.headers.get("x-forwarded-for") if self.settings.trust_proxy_headers else None
        )
        raw_ip = forwarded.split(",", 1)[0].strip() if forwarded else None
        if not raw_ip and request.client:
            raw_ip = request.client.host
        try:
            address = ipaddress.ip_address(raw_ip or "")
        except ValueError:
            return JSONResponse(status_code=403, content={"detail": "IP запрещён"})
        if not any(address in network for network in self.networks):
            return JSONResponse(status_code=403, content={"detail": "IP запрещён"})
        return await call_next(request)
