from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import require_roles
from app.enums import PrivacyRequestStatus, PrivacyRequestType, UserRole
from app.models import Conversation, PrivacyRequest, User
from app.schemas import PrivacyRequestCreate, PrivacyRequestRead
from app.services.privacy import PrivacyService, RetentionService

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _request(db: Session, request_id: str, org_id: str) -> PrivacyRequest:
    """Реализовать внутренний этап request step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = db.scalar(
        select(PrivacyRequest).where(
            PrivacyRequest.id == request_id,
            PrivacyRequest.organization_id == org_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Privacy request не найден")
    return item


@router.get("/requests", response_model=list[PrivacyRequestRead])
def list_requests(
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> list[PrivacyRequest]:
    """Прочитать requests. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(PrivacyRequest)
            .where(PrivacyRequest.organization_id == user.organization_id)
            .order_by(PrivacyRequest.created_at.desc())
        ).all()
    )


@router.post("/requests", response_model=PrivacyRequestRead, status_code=201)
def create_request(
    payload: PrivacyRequestCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> PrivacyRequest:
    """Создать request. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    if payload.request_type == PrivacyRequestType.DELETE and user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Удаление данных доступно только владельцу")
    if payload.conversation_id:
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.organization_id == user.organization_id,
            )
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="Диалог не найден")
    item = PrivacyRequest(
        organization_id=user.organization_id,
        request_type=payload.request_type,
        telegram_user_id=payload.telegram_user_id,
        telegram_chat_id=payload.telegram_chat_id,
        conversation_id=payload.conversation_id,
        requested_by_id=user.id,
        status=PrivacyRequestStatus.PENDING,
    )
    db.add(item)
    write_audit(
        db,
        actor=user,
        action="privacy.request_created",
        entity_type="privacy_request",
        entity_id=item.id,
        details={"type": item.request_type.value},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/requests/{request_id}/process", response_model=PrivacyRequestRead)
def process_request_now(
    request_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> PrivacyRequest:
    """Выполнить process request now. Операция координирует ограниченные побочные эффекты и
    возвращает детерминированный результат.
    """
    item = _request(db, request_id, user.organization_id)
    if item.status not in {PrivacyRequestStatus.PENDING, PrivacyRequestStatus.FAILED}:
        raise HTTPException(status_code=409, detail="Запрос уже обрабатывается или завершён")
    item.status = PrivacyRequestStatus.PENDING
    item.error_message = None
    service = PrivacyService(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.cipher,
        request.app.state.storage,
    )
    service.process(db, item)
    db.refresh(item)
    return item


@router.get("/requests/{request_id}/download")
def download_export(
    request_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Response:
    """Выполнить операцию download export. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    item = _request(db, request_id, user.organization_id)
    if item.request_type != PrivacyRequestType.EXPORT:
        raise HTTPException(status_code=409, detail="Это не запрос экспорта")
    try:
        data = PrivacyService(
            request.app.state.session_factory,
            request.app.state.settings,
            request.app.state.cipher,
            request.app.state.storage,
        ).read_export(item)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user,
        action="privacy.export_downloaded",
        entity_type="privacy_request",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return Response(
        content=data,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="privacy-export-{item.id}.json"'},
    )


@router.post("/retention/run", response_model=dict[str, int])
def run_retention(
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Выполнить run retention. Операция координирует ограниченные побочные эффекты и возвращает
    детерминированный результат.
    """
    stats = RetentionService(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.storage,
    ).run()
    write_audit(
        db,
        actor=user,
        action="privacy.retention_run",
        entity_type="organization",
        entity_id=user.organization_id,
        details=stats,
        request=request,
    )
    db.commit()
    return stats
