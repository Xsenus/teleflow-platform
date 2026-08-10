from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.enums import BlackoutScope, UserRole
from app.models import Destination, PublishingBlackout, TelegramConnection, User
from app.schemas import (
    BlackoutEvaluationRead,
    BlackoutMatchRead,
    MessageResponse,
    PublishingBlackoutCreate,
    PublishingBlackoutPatch,
    PublishingBlackoutRead,
)
from app.services.blackouts import evaluate_blackouts

router = APIRouter(prefix="/blackouts", tags=["blackouts"])


def _get_blackout(db: Session, blackout_id: str, organization_id: str) -> PublishingBlackout:
    """Реализовать внутренний этап get blackout step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    blackout = db.scalar(
        select(PublishingBlackout).where(
            PublishingBlackout.id == blackout_id,
            PublishingBlackout.organization_id == organization_id,
        )
    )
    if blackout is None:
        raise HTTPException(status_code=404, detail="Запрет публикаций не найден")
    return blackout


def _validate_scope_references(
    db: Session,
    *,
    organization_id: str,
    payload: PublishingBlackoutCreate,
) -> None:
    """Реализовать внутренний этап validate scope references step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    if payload.connection_id:
        connection = db.scalar(
            select(TelegramConnection).where(
                TelegramConnection.id == payload.connection_id,
                TelegramConnection.organization_id == organization_id,
            )
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="Telegram-подключение не найдено")
    if payload.destination_id:
        destination = db.scalar(
            select(Destination).where(
                Destination.id == payload.destination_id,
                Destination.organization_id == organization_id,
            )
        )
        if destination is None:
            raise HTTPException(status_code=404, detail="Назначение не найдено")
        if payload.connection_id and destination.connection_id != payload.connection_id:
            raise HTTPException(
                status_code=422,
                detail="Назначение не относится к выбранному Telegram-подключению",
            )


@router.get("", response_model=list[PublishingBlackoutRead])
def list_blackouts(
    enabled: bool | None = None,
    scope: BlackoutScope | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PublishingBlackout]:
    """Прочитать blackouts. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(PublishingBlackout).where(
        PublishingBlackout.organization_id == user.organization_id
    )
    if enabled is not None:
        stmt = stmt.where(PublishingBlackout.enabled == enabled)
    if scope is not None:
        stmt = stmt.where(PublishingBlackout.scope == scope)
    return list(db.scalars(stmt.order_by(PublishingBlackout.created_at.desc())).all())


@router.post("", response_model=PublishingBlackoutRead, status_code=201)
def create_blackout(
    payload: PublishingBlackoutCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> PublishingBlackout:
    """Создать blackout. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    _validate_scope_references(db, organization_id=user.organization_id, payload=payload)
    blackout = PublishingBlackout(
        organization_id=user.organization_id,
        created_by_id=user.id,
        **payload.model_dump(),
    )
    db.add(blackout)
    db.flush()
    write_audit(
        db,
        action="publishing_blackout.created",
        actor=user,
        entity_type="publishing_blackout",
        entity_id=blackout.id,
        details={
            "scope": blackout.scope.value,
            "kind": blackout.kind.value,
            "connection_id": blackout.connection_id,
            "destination_id": blackout.destination_id,
            "enabled": blackout.enabled,
        },
        request=request,
    )
    db.commit()
    db.refresh(blackout)
    return blackout


@router.patch("/{blackout_id}", response_model=PublishingBlackoutRead)
def update_blackout(
    blackout_id: str,
    payload: PublishingBlackoutPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> PublishingBlackout:
    """Обновить blackout. Переход применяется только после проверки его предусловий."""
    blackout = _get_blackout(db, blackout_id, user.organization_id)
    merged = {
        "title": blackout.title,
        "reason": blackout.reason,
        "scope": blackout.scope,
        "kind": blackout.kind,
        "connection_id": blackout.connection_id,
        "destination_id": blackout.destination_id,
        "enabled": blackout.enabled,
        "starts_at": blackout.starts_at,
        "ends_at": blackout.ends_at,
        "timezone_name": blackout.timezone_name,
        "weekdays": blackout.weekdays,
        "start_time": blackout.start_time,
        "end_time": blackout.end_time,
    }
    merged.update(payload.model_dump(exclude_unset=True))
    # A PATCH can switch scope/kind while omitting fields that belong to the
    # previous variant. Normalize mutually exclusive fields before validating
    # the merged object so stale target/window data cannot survive the switch.
    if merged["scope"] == BlackoutScope.ORGANIZATION:
        merged["connection_id"] = None
        merged["destination_id"] = None
    elif merged["scope"] == BlackoutScope.CONNECTION:
        merged["destination_id"] = None
    elif merged["scope"] == BlackoutScope.DESTINATION and not payload.connection_id:
        merged["connection_id"] = None

    if str(getattr(merged["kind"], "value", merged["kind"])) == "one_time":
        merged["timezone_name"] = None
        merged["weekdays"] = []
        merged["start_time"] = None
        merged["end_time"] = None
    else:
        merged["starts_at"] = None
        merged["ends_at"] = None
    try:
        validated = PublishingBlackoutCreate.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    _validate_scope_references(db, organization_id=user.organization_id, payload=validated)
    for key, value in validated.model_dump().items():
        setattr(blackout, key, value)
    write_audit(
        db,
        action="publishing_blackout.updated",
        actor=user,
        entity_type="publishing_blackout",
        entity_id=blackout.id,
        details={
            "changed_fields": sorted(payload.model_fields_set),
            "enabled": blackout.enabled,
        },
        request=request,
    )
    db.commit()
    db.refresh(blackout)
    return blackout


@router.delete("/{blackout_id}", response_model=MessageResponse)
def delete_blackout(
    blackout_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete blackout. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    blackout = _get_blackout(db, blackout_id, user.organization_id)
    write_audit(
        db,
        action="publishing_blackout.deleted",
        actor=user,
        entity_type="publishing_blackout",
        entity_id=blackout.id,
        details={"title": blackout.title},
        request=request,
    )
    db.delete(blackout)
    db.commit()
    return MessageResponse(message="Запрет публикаций удалён")


@router.get("/evaluate/current", response_model=BlackoutEvaluationRead)
def evaluate_current_blackouts(
    connection_id: str | None = None,
    destination_id: str | None = None,
    at: datetime | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BlackoutEvaluationRead:
    """Выполнить операцию evaluate current blackouts. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    if connection_id:
        connection = db.scalar(
            select(TelegramConnection).where(
                TelegramConnection.id == connection_id,
                TelegramConnection.organization_id == user.organization_id,
            )
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="Telegram-подключение не найдено")
    if destination_id:
        destination = db.scalar(
            select(Destination).where(
                Destination.id == destination_id,
                Destination.organization_id == user.organization_id,
            )
        )
        if destination is None:
            raise HTTPException(status_code=404, detail="Назначение не найдено")
        connection_id = connection_id or destination.connection_id
    decision = evaluate_blackouts(
        db,
        organization_id=user.organization_id,
        connection_id=connection_id,
        destination_id=destination_id,
        at=at,
    )
    return BlackoutEvaluationRead(
        active=decision.active,
        defer_until=decision.defer_until,
        matches=[
            BlackoutMatchRead(
                blackout_id=item.blackout_id,
                title=item.title,
                reason=item.reason,
                scope=item.scope,
                active_until=item.active_until,
            )
            for item in decision.matches
        ],
    )
