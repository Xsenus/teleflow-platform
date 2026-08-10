from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.enums import CandidateStatus, ConsentStatus, ConversationStatus, UserRole
from app.models import CandidateProfile, Conversation, ConversationMessage, User
from app.schemas import (
    CandidateContactResponse,
    CandidatePatch,
    CandidateRead,
    ConversationMessageBodyResponse,
    ConversationMessageRead,
    ConversationPatch,
    ConversationRead,
    ConversationReplyRequest,
    MessageResponse,
)
from app.services.inbound import InboundService
from app.services.outbox import enqueue_event

router = APIRouter(tags=["conversations"])


def _conversation(db: Session, conversation_id: str, org_id: str) -> Conversation:
    """Реализовать внутренний этап conversation step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == org_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return item


def _candidate(db: Session, candidate_id: str, org_id: str) -> CandidateProfile:
    """Реализовать внутренний этап candidate step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = db.scalar(
        select(CandidateProfile).where(
            CandidateProfile.id == candidate_id,
            CandidateProfile.organization_id == org_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Кандидат не найден")
    return item


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations(
    status_filter: ConversationStatus | None = Query(default=None, alias="status"),
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Conversation]:
    """Прочитать conversations. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(Conversation).where(Conversation.organization_id == user.organization_id)
    if status_filter:
        stmt = stmt.where(Conversation.status == status_filter)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Conversation.username.ilike(pattern),
                Conversation.first_name.ilike(pattern),
                Conversation.last_name.ilike(pattern),
                Conversation.vacancy_key.ilike(pattern),
            )
        )
    stmt = (
        stmt.order_by(Conversation.last_message_at.desc().nullslast()).offset(offset).limit(limit)
    )
    return list(db.scalars(stmt).all())


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Conversation:
    """Прочитать conversation. Значение возвращается без несвязанных изменений состояния."""
    return _conversation(db, conversation_id, user.organization_id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
def patch_conversation(
    conversation_id: str,
    payload: ConversationPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> Conversation:
    """Обновить conversation. Переход применяется только после проверки его предусловий."""
    item = _conversation(db, conversation_id, user.organization_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("assigned_user_id"):
        assignee = db.scalar(
            select(User).where(
                User.id == values["assigned_user_id"],
                User.organization_id == user.organization_id,
                User.is_active.is_(True),
            )
        )
        if not assignee:
            raise HTTPException(status_code=422, detail="Исполнитель не найден")
    for key, value in values.items():
        setattr(item, key, value)
    if item.status in {
        ConversationStatus.HUMAN_HANDOFF,
        ConversationStatus.BLOCKED,
        ConversationStatus.CLOSED,
    }:
        item.ai_enabled = False
    if item.consent_status in {ConsentStatus.DECLINED, ConsentStatus.REVOKED}:
        item.ai_enabled = False
    write_audit(
        db,
        actor=user,
        action="conversation.updated",
        entity_type="conversation",
        entity_id=item.id,
        details={"fields": sorted(values)},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageRead],
)
def list_messages(
    conversation_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    before: datetime | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationMessage]:
    """Прочитать messages. Значение возвращается без несвязанных изменений состояния."""
    item = _conversation(db, conversation_id, user.organization_id)
    stmt = select(ConversationMessage).where(
        ConversationMessage.organization_id == user.organization_id,
        ConversationMessage.conversation_id == item.id,
    )
    if before:
        stmt = stmt.where(ConversationMessage.created_at < before)
    return list(
        db.scalars(stmt.order_by(ConversationMessage.created_at.desc()).limit(limit)).all()
    )[::-1]


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/body",
    response_model=ConversationMessageBodyResponse,
)
def reveal_message_body(
    conversation_id: str,
    message_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> ConversationMessageBodyResponse:
    """Выполнить операцию reveal message body. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    _conversation(db, conversation_id, user.organization_id)
    item = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == message_id,
            ConversationMessage.organization_id == user.organization_id,
            ConversationMessage.conversation_id == conversation_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    body = ""
    if item.body_enc:
        try:
            body = request.app.state.cipher.decrypt(
                item.body_enc, context=f"conversation-message:{item.id}:body"
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Не удалось расшифровать сообщение"
            ) from exc
    write_audit(
        db,
        actor=user,
        action="conversation.message_revealed",
        entity_type="conversation_message",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return ConversationMessageBodyResponse(id=item.id, body=body)


@router.post(
    "/conversations/{conversation_id}/reply",
    response_model=ConversationMessageRead,
)
def reply_to_conversation(
    conversation_id: str,
    payload: ConversationReplyRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> ConversationMessage:
    """Выполнить операцию reply to conversation. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    item = _conversation(db, conversation_id, user.organization_id)
    if item.status in {ConversationStatus.BLOCKED, ConversationStatus.CLOSED}:
        raise HTTPException(status_code=409, detail="Диалог закрыт или заблокирован")
    try:
        message = InboundService(
            request.app.state.session_factory,
            request.app.state.settings,
            request.app.state.cipher,
        ).send_operator_reply(db, conversation=item, text=payload.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось отправить ответ: {exc}") from exc
    item.assigned_user_id = user.id
    write_audit(
        db,
        actor=user,
        action="conversation.operator_replied",
        entity_type="conversation",
        entity_id=item.id,
        details={"message_id": message.id},
        request=request,
    )
    db.commit()
    db.refresh(message)
    return message


@router.post("/conversations/{conversation_id}/close", response_model=MessageResponse)
def close_conversation(
    conversation_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить close conversation. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    item = _conversation(db, conversation_id, user.organization_id)
    item.status = ConversationStatus.CLOSED
    item.ai_enabled = False
    write_audit(
        db,
        actor=user,
        action="conversation.closed",
        entity_type="conversation",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Диалог закрыт")


@router.get("/candidates", response_model=list[CandidateRead])
def list_candidates(
    status_filter: CandidateStatus | None = Query(default=None, alias="status"),
    vacancy_key: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CandidateProfile]:
    """Прочитать candidates. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(CandidateProfile).where(CandidateProfile.organization_id == user.organization_id)
    if status_filter:
        stmt = stmt.where(CandidateProfile.status == status_filter)
    if vacancy_key:
        stmt = stmt.where(CandidateProfile.vacancy_key == vacancy_key)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                CandidateProfile.full_name.ilike(pattern),
                CandidateProfile.city.ilike(pattern),
                CandidateProfile.summary.ilike(pattern),
            )
        )
    return list(
        db.scalars(
            stmt.order_by(CandidateProfile.updated_at.desc()).offset(offset).limit(limit)
        ).all()
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateRead)
def get_candidate(
    candidate_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CandidateProfile:
    """Прочитать candidate. Значение возвращается без несвязанных изменений состояния."""
    return _candidate(db, candidate_id, user.organization_id)


@router.patch("/candidates/{candidate_id}", response_model=CandidateRead)
def patch_candidate(
    candidate_id: str,
    payload: CandidatePatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> CandidateProfile:
    """Обновить candidate. Переход применяется только после проверки его предусловий."""
    item = _candidate(db, candidate_id, user.organization_id)
    values = payload.model_dump(exclude_unset=True)
    phone = values.pop("phone", None)
    email = values.pop("email", None)
    for key, value in values.items():
        setattr(item, key, value)
    if phone is not None:
        item.phone_enc = (
            request.app.state.cipher.encrypt(phone, context=f"candidate:{item.id}:phone")
            if phone
            else None
        )
    if email is not None:
        raw_email = str(email)
        item.email_enc = (
            request.app.state.cipher.encrypt(raw_email, context=f"candidate:{item.id}:email")
            if raw_email
            else None
        )
    enqueue_event(
        db,
        organization_id=item.organization_id,
        event_type="candidate.updated",
        aggregate_type="candidate",
        aggregate_id=item.id,
        payload={
            "candidate_id": item.id,
            "conversation_id": item.conversation_id,
            "full_name": item.full_name,
            "city": item.city,
            "age": item.age,
            "experience": item.experience,
            "schedule": item.schedule,
            "vacancy_key": item.vacancy_key,
            "status": item.status.value,
            "summary": item.summary,
            "consent_to_storage": item.consent_to_storage,
        },
    )
    write_audit(
        db,
        actor=user,
        action="candidate.updated",
        entity_type="candidate",
        entity_id=item.id,
        details={
            "fields": sorted(values)
            + (["phone"] if phone is not None else [])
            + (["email"] if email is not None else [])
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.get("/candidates/{candidate_id}/contact", response_model=CandidateContactResponse)
def reveal_candidate_contact(
    candidate_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> CandidateContactResponse:
    """Выполнить операцию reveal candidate contact. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    item = _candidate(db, candidate_id, user.organization_id)
    phone = None
    email = None
    try:
        if item.phone_enc:
            phone = request.app.state.cipher.decrypt(
                item.phone_enc, context=f"candidate:{item.id}:phone"
            )
        if item.email_enc:
            email = request.app.state.cipher.decrypt(
                item.email_enc, context=f"candidate:{item.id}:email"
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Не удалось расшифровать контакты") from exc
    write_audit(
        db,
        actor=user,
        action="candidate.contact_revealed",
        entity_type="candidate",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return CandidateContactResponse(candidate_id=item.id, phone=phone, email=email)


@router.get("/candidates-export.csv")
def export_candidates_csv(
    request: Request,
    include_contacts: bool = False,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Выполнить операцию export candidates csv. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    items = list(
        db.scalars(
            select(CandidateProfile)
            .where(CandidateProfile.organization_id == user.organization_id)
            .order_by(CandidateProfile.created_at)
        ).all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [
        "id",
        "conversation_id",
        "full_name",
        "city",
        "age",
        "experience",
        "schedule",
        "vacancy_key",
        "status",
        "summary",
        "consent_to_storage",
        "created_at",
    ]
    if include_contacts:
        headers += ["phone", "email"]
    writer.writerow(headers)
    for item in items:
        row = [
            item.id,
            item.conversation_id,
            item.full_name,
            item.city,
            item.age,
            item.experience,
            item.schedule,
            item.vacancy_key,
            item.status.value,
            item.summary,
            item.consent_to_storage,
            item.created_at.isoformat(),
        ]
        if include_contacts:
            phone = (
                request.app.state.cipher.decrypt(
                    item.phone_enc, context=f"candidate:{item.id}:phone"
                )
                if item.phone_enc
                else None
            )
            email = (
                request.app.state.cipher.decrypt(
                    item.email_enc, context=f"candidate:{item.id}:email"
                )
                if item.email_enc
                else None
            )
            row += [phone, email]
        writer.writerow(row)
    write_audit(
        db,
        actor=user,
        action="candidate.csv_exported",
        entity_type="candidate",
        details={"rows": len(items), "include_contacts": include_contacts},
        request=request,
    )
    db.commit()
    data = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([data.encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="candidates.csv"'},
    )
