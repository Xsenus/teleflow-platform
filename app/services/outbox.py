from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import socket
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.enums import IntegrationKind, OutboxStatus
from app.models import IntegrationEndpoint, OutboxEvent, utcnow
from app.observability import OUTBOX_RESULTS
from app.services.crypto import SecretCipher
from app.services.storage import StorageService
from app.services.url_security import validate_outbound_url

logger = logging.getLogger(__name__)


def enqueue_event(
    db: Session,
    *,
    organization_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    target_endpoint_ids: list[str] | None = None,
) -> OutboxEvent:
    """Выполнить операцию enqueue event. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    event = OutboxEvent(
        organization_id=organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        target_endpoint_ids=target_endpoint_ids,
        delivery_state={},
        status=OutboxStatus.PENDING,
        due_at=utcnow(),
    )
    db.add(event)
    return event


class OutboxService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        cipher: SecretCipher,
        *,
        worker_id: str | None = None,
        storage: StorageService | None = None,
    ):
        """Инициализировать OutboxService with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.session_factory = session_factory
        self.settings = settings
        self.cipher = cipher
        self.worker_id = worker_id or f"{socket.gethostname()}:outbox"
        self.storage = storage or StorageService(settings)

    def recover_stale(self) -> int:
        """Выполнить операцию recover stale класса OutboxService. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        now = utcnow()
        stale = now - timedelta(minutes=self.settings.outbox_lease_minutes)
        with self.session_factory() as db:
            items = list(
                db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.status == OutboxStatus.PROCESSING,
                        OutboxEvent.locked_at < stale,
                    )
                ).all()
            )
            for item in items:
                item.status = OutboxStatus.RETRY
                item.locked_at = None
                item.locked_by = None
                item.due_at = now + timedelta(seconds=30)
            db.commit()
            return len(items)

    def process_next(self) -> bool:
        """Выполнить process next класса OutboxService. Операция координирует ограниченные побочные
        эффекты и возвращает детерминированный результат.
        """
        now = utcnow()
        with self.session_factory() as db:
            event = db.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.RETRY]),
                    OutboxEvent.due_at <= now,
                )
                .order_by(OutboxEvent.due_at, OutboxEvent.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not event:
                return False
            event.status = OutboxStatus.PROCESSING
            event.locked_at = now
            event.locked_by = self.worker_id
            event.attempt_count += 1
            db.commit()

            try:
                endpoints = list(
                    db.scalars(
                        select(IntegrationEndpoint).where(
                            IntegrationEndpoint.organization_id == event.organization_id
                        )
                    ).all()
                )
                endpoint_map = {endpoint.id: endpoint for endpoint in endpoints}
                if event.target_endpoint_ids is None:
                    targets = [
                        endpoint.id
                        for endpoint in endpoints
                        if endpoint.is_active
                        and (not endpoint.event_types or event.event_type in endpoint.event_types)
                    ]
                    event.target_endpoint_ids = targets
                    db.commit()
                else:
                    targets = list(event.target_endpoint_ids)

                state = dict(event.delivery_state or {})
                for endpoint_id in targets:
                    previous = state.get(endpoint_id) or {}
                    if previous.get("status") in {"delivered", "skipped"}:
                        continue
                    endpoint = endpoint_map.get(endpoint_id)
                    if not endpoint or not endpoint.is_active:
                        state[endpoint_id] = {
                            "status": "skipped",
                            "reason": "endpoint_missing_or_inactive",
                            "updated_at": now.isoformat(),
                        }
                        event.delivery_state = dict(state)
                        db.commit()
                        continue
                    try:
                        self._deliver(endpoint, event)
                    except Exception as exc:
                        endpoint.last_error_message = str(exc)[:2000]
                        state[endpoint_id] = {
                            "status": "retry",
                            "attempt": event.attempt_count,
                            "error": type(exc).__name__,
                            "updated_at": now.isoformat(),
                        }
                        event.delivery_state = dict(state)
                        db.commit()
                        raise
                    endpoint.last_delivery_at = now
                    endpoint.last_error_message = None
                    state[endpoint_id] = {
                        "status": "delivered",
                        "attempt": event.attempt_count,
                        "updated_at": now.isoformat(),
                    }
                    # Persist each successful endpoint before moving to the next one.
                    # A receiver should still deduplicate by X-TeleFlow-Event-ID because
                    # no distributed system can make an external HTTP append exactly-once.
                    event.delivery_state = dict(state)
                    db.commit()

                event.status = OutboxStatus.DELIVERED
                OUTBOX_RESULTS.labels("delivered", event.event_type).inc()
                event.delivered_at = now
                event.last_error_message = None
                event.locked_at = None
                event.locked_by = None
                event.delivery_state = dict(state)
                db.commit()
            except Exception as exc:
                logger.exception("Outbox delivery failed event=%s", event.id)
                event.last_error_message = str(exc)[:2000]
                event.locked_at = None
                event.locked_by = None
                if event.attempt_count >= event.max_attempts:
                    event.status = OutboxStatus.DEAD
                    OUTBOX_RESULTS.labels("dead", event.event_type).inc()
                else:
                    event.status = OutboxStatus.RETRY
                    OUTBOX_RESULTS.labels("retry", event.event_type).inc()
                    delay = min(3600, 30 * (2 ** min(event.attempt_count - 1, 7)))
                    event.due_at = now + timedelta(seconds=delay)
                db.commit()
            return True

    def _deliver(self, endpoint: IntegrationEndpoint, event: OutboxEvent) -> None:
        """Реализовать внутренний этап deliver step класса OutboxService. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        config = self._config(endpoint)
        if endpoint.kind == IntegrationKind.WEBHOOK:
            self._webhook(config, event)
        elif endpoint.kind == IntegrationKind.GOOGLE_SHEETS:
            self._google_sheets(config, event)
        elif endpoint.kind == IntegrationKind.CSV_EXPORT:
            self._csv(config, event)
        else:
            raise ValueError(f"Неподдерживаемая интеграция: {endpoint.kind}")

    def _config(self, endpoint: IntegrationEndpoint) -> dict[str, Any]:
        """Реализовать внутренний этап config step класса OutboxService. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        if not endpoint.config_enc:
            return {}
        return self.cipher.decrypt_json(
            endpoint.config_enc, context=f"integration:{endpoint.id}:config"
        )

    def _webhook(self, config: dict[str, Any], event: OutboxEvent) -> None:
        """Реализовать внутренний этап webhook step класса OutboxService. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        url = str(config.get("url") or "")
        validate_outbound_url(
            url,
            allow_private=self.settings.allow_private_integration_urls,
            require_https=self.settings.is_production,
        )
        body = json.dumps(
            {
                "id": event.id,
                "type": event.event_type,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "created_at": event.created_at.isoformat(),
                "data": event.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        secret = str(config.get("secret") or "")
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-TeleFlow-Event-ID": event.id,
            "X-TeleFlow-Signature": f"sha256={signature}",
        }
        with httpx.Client(timeout=self.settings.webhook_timeout_seconds) as client:
            response = client.post(url, content=body, headers=headers)
        response.raise_for_status()

    def _google_sheets(self, config: dict[str, Any], event: OutboxEvent) -> None:
        """Реализовать внутренний этап google sheets step класса OutboxService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        spreadsheet_id = str(config.get("spreadsheet_id") or "")
        range_name = str(config.get("range") or "Candidates!A:Z")
        service_account = config.get("service_account")
        if not spreadsheet_id or not isinstance(service_account, dict):
            raise ValueError("Google Sheets integration is incomplete")
        token = self._google_access_token(service_account)
        values = [
            [
                event.id,
                event.event_type,
                event.aggregate_id,
                event.created_at.isoformat(),
                json.dumps(event.payload, ensure_ascii=False, default=str),
            ]
        ]
        url = (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{spreadsheet_id}/values/{range_name}:append"
        )
        with httpx.Client(timeout=self.settings.webhook_timeout_seconds) as client:
            response = client.post(
                url,
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                headers={"Authorization": f"Bearer {token}"},
                json={"values": values},
            )
        response.raise_for_status()

    def _google_access_token(self, service_account: dict[str, Any]) -> str:
        """Реализовать внутренний этап google access token step класса OutboxService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        import time

        import jwt

        now = int(time.time())
        token_uri = str(service_account.get("token_uri") or "https://oauth2.googleapis.com/token")
        if token_uri not in {
            "https://oauth2.googleapis.com/token",
            "https://accounts.google.com/o/oauth2/token",
        }:
            raise ValueError("Неподдерживаемый Google OAuth token_uri")
        assertion = jwt.encode(
            {
                "iss": service_account["client_email"],
                "scope": "https://www.googleapis.com/auth/spreadsheets",
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            service_account["private_key"],
            algorithm="RS256",
        )
        with httpx.Client(timeout=self.settings.webhook_timeout_seconds) as client:
            response = client.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        response.raise_for_status()
        return str(response.json()["access_token"])

    def _csv(self, config: dict[str, Any], event: OutboxEvent) -> None:
        # One immutable object per outbox event avoids lost updates when several
        # workers export concurrently and works identically for local and S3 storage.
        """Реализовать внутренний этап csv step класса OutboxService. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        configured = str(
            config.get("storage_key")
            or config.get("relative_path")
            or f"integrations/{event.organization_id}/events"
        )
        base = PurePosixPath(configured.strip("/"))
        if base.suffix.lower() == ".csv":
            base = base.parent / base.stem
        key = str(base / f"{event.id}.csv")

        rows: list[list[str]] = [
            ["event_id", "event_type", "aggregate_id", "created_at", "payload"],
            [
                event.id,
                event.event_type,
                event.aggregate_id,
                event.created_at.isoformat(),
                json.dumps(event.payload, ensure_ascii=False, default=str),
            ],
        ]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerows(rows)
        self.storage.put_bytes(
            key,
            buffer.getvalue().encode("utf-8-sig"),
            content_type="text/csv",
        )
