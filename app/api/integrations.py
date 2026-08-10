from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.enums import IntegrationKind, OutboxStatus, UserRole
from app.models import IntegrationEndpoint, OutboxEvent, User, utcnow
from app.schemas import (
    IntegrationCreate,
    IntegrationPatch,
    IntegrationRead,
    MessageResponse,
    OutboxEventRead,
)
from app.services.outbox import enqueue_event
from app.services.url_security import UnsafeURL, validate_outbound_url

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _endpoint(db: Session, endpoint_id: str, org_id: str) -> IntegrationEndpoint:
    """Реализовать внутренний этап endpoint step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = db.scalar(
        select(IntegrationEndpoint).where(
            IntegrationEndpoint.id == endpoint_id,
            IntegrationEndpoint.organization_id == org_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Интеграция не найдена")
    return item


def _validate_config(kind: IntegrationKind, config: dict, request: Request) -> dict:
    """Реализовать внутренний этап validate config step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    clean = dict(config)
    if kind == IntegrationKind.WEBHOOK:
        url = str(clean.get("url") or "")
        try:
            validate_outbound_url(
                url,
                allow_private=request.app.state.settings.allow_private_integration_urls,
                require_https=request.app.state.settings.is_production,
            )
        except UnsafeURL as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        clean["url"] = url
        clean.setdefault("secret", secrets.token_urlsafe(32))
    elif kind == IntegrationKind.GOOGLE_SHEETS:
        if not clean.get("spreadsheet_id") or not isinstance(clean.get("service_account"), dict):
            raise HTTPException(
                status_code=422,
                detail="Нужны spreadsheet_id и service_account JSON",
            )
        account = clean["service_account"]
        for key in ("client_email", "private_key"):
            if not account.get(key):
                raise HTTPException(status_code=422, detail=f"service_account.{key} отсутствует")
        clean.setdefault("range", "Candidates!A:Z")
    elif kind == IntegrationKind.CSV_EXPORT:
        relative = str(clean.get("relative_path") or "integrations/events.csv")
        if relative.startswith(("/", "\\")) or ".." in relative.replace("\\", "/").split("/"):
            raise HTTPException(status_code=422, detail="Некорректный relative_path")
        clean["relative_path"] = relative
    return clean


@router.get("", response_model=list[IntegrationRead])
def list_integrations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IntegrationEndpoint]:
    """Прочитать integrations. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(IntegrationEndpoint)
            .where(IntegrationEndpoint.organization_id == user.organization_id)
            .order_by(IntegrationEndpoint.created_at.desc())
        ).all()
    )


@router.post("", response_model=IntegrationRead, status_code=201)
def create_integration(
    payload: IntegrationCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> IntegrationEndpoint:
    """Создать integration. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    config = _validate_config(payload.kind, payload.config, request)
    item = IntegrationEndpoint(
        organization_id=user.organization_id,
        name=payload.name,
        kind=payload.kind,
        config_enc=None,
        event_types=payload.event_types,
        is_active=payload.is_active,
        created_by_id=user.id,
    )
    db.add(item)
    db.flush()
    item.config_enc = request.app.state.cipher.encrypt_json(
        config, context=f"integration:{item.id}:config"
    )
    write_audit(
        db,
        actor=user,
        action="integration.created",
        entity_type="integration",
        entity_id=item.id,
        details={"kind": item.kind.value, "events": item.event_types},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.patch("/{endpoint_id}", response_model=IntegrationRead)
def patch_integration(
    endpoint_id: str,
    payload: IntegrationPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> IntegrationEndpoint:
    """Обновить integration. Переход применяется только после проверки его предусловий."""
    item = _endpoint(db, endpoint_id, user.organization_id)
    values = payload.model_dump(exclude_unset=True)
    config = values.pop("config", None)
    for key, value in values.items():
        setattr(item, key, value)
    if config is not None:
        clean = _validate_config(item.kind, config, request)
        item.config_enc = request.app.state.cipher.encrypt_json(
            clean, context=f"integration:{item.id}:config"
        )
    write_audit(
        db,
        actor=user,
        action="integration.updated",
        entity_type="integration",
        entity_id=item.id,
        details={"fields": sorted(values) + (["config"] if config is not None else [])},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/{endpoint_id}/test", response_model=OutboxEventRead)
def test_integration(
    endpoint_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> OutboxEvent:
    """Проверить сценарий integration. Тест завершается ошибкой при нарушении зафиксированного
    инварианта.
    """
    item = _endpoint(db, endpoint_id, user.organization_id)
    event = enqueue_event(
        db,
        organization_id=user.organization_id,
        event_type="integration.test",
        aggregate_type="integration",
        aggregate_id=item.id,
        payload={"message": "TeleFlow integration test", "endpoint_id": item.id},
        target_endpoint_ids=[item.id],
    )
    write_audit(
        db,
        actor=user,
        action="integration.test_enqueued",
        entity_type="integration",
        entity_id=item.id,
        details={"event_id": event.id},
        request=request,
    )
    db.commit()
    db.refresh(event)
    return event


@router.delete("/{endpoint_id}", response_model=MessageResponse)
def delete_integration(
    endpoint_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete integration. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    item = _endpoint(db, endpoint_id, user.organization_id)
    db.delete(item)
    write_audit(
        db,
        actor=user,
        action="integration.deleted",
        entity_type="integration",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Интеграция удалена")


@router.get("/outbox/events", response_model=list[OutboxEventRead])
def list_outbox_events(
    status_filter: OutboxStatus | None = None,
    limit: int = 200,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OutboxEvent]:
    """Прочитать outbox events. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(OutboxEvent).where(OutboxEvent.organization_id == user.organization_id)
    if status_filter:
        stmt = stmt.where(OutboxEvent.status == status_filter)
    return list(
        db.scalars(
            stmt.order_by(OutboxEvent.created_at.desc()).limit(min(max(limit, 1), 1000))
        ).all()
    )


@router.post("/outbox/{event_id}/retry", response_model=OutboxEventRead)
def retry_outbox_event(
    event_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> OutboxEvent:
    """Выполнить операцию retry outbox event. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    item = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.id == event_id,
            OutboxEvent.organization_id == user.organization_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Outbox event не найден")
    if item.status not in {OutboxStatus.FAILED, OutboxStatus.DEAD, OutboxStatus.RETRY}:
        raise HTTPException(status_code=409, detail="Событие нельзя поставить на повтор")
    item.status = OutboxStatus.RETRY
    item.due_at = utcnow()
    item.last_error_message = None
    item.locked_at = None
    item.locked_by = None
    write_audit(
        db,
        actor=user,
        action="outbox.retry_requested",
        entity_type="outbox_event",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item
