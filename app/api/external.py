from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CandidateProfile, Conversation
from app.schemas import CandidateContactResponse, CandidateRead, ConversationRead, MessageResponse
from app.service_auth import ServicePrincipal, require_scope
from app.services.outbox import enqueue_event

router = APIRouter(prefix="/external", tags=["external-api"])


@router.get("/candidates", response_model=list[CandidateRead])
def external_candidates(
    limit: int = 100,
    principal: ServicePrincipal = Depends(require_scope("candidates:read")),
    db: Session = Depends(get_db),
) -> list[CandidateProfile]:
    """Выполнить операцию external candidates. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return list(
        db.scalars(
            select(CandidateProfile)
            .where(CandidateProfile.organization_id == principal.organization_id)
            .order_by(CandidateProfile.updated_at.desc())
            .limit(min(max(limit, 1), 500))
        ).all()
    )


@router.get("/candidates/{candidate_id}/contact", response_model=CandidateContactResponse)
def external_candidate_contact(
    candidate_id: str,
    request: Request,
    principal: ServicePrincipal = Depends(require_scope("candidates:contacts")),
    db: Session = Depends(get_db),
) -> CandidateContactResponse:
    """Выполнить операцию external candidate contact. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    item = db.scalar(
        select(CandidateProfile).where(
            CandidateProfile.id == candidate_id,
            CandidateProfile.organization_id == principal.organization_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Кандидат не найден")
    phone = (
        request.app.state.cipher.decrypt(item.phone_enc, context=f"candidate:{item.id}:phone")
        if item.phone_enc
        else None
    )
    email = (
        request.app.state.cipher.decrypt(item.email_enc, context=f"candidate:{item.id}:email")
        if item.email_enc
        else None
    )
    return CandidateContactResponse(candidate_id=item.id, phone=phone, email=email)


@router.get("/conversations", response_model=list[ConversationRead])
def external_conversations(
    limit: int = 100,
    principal: ServicePrincipal = Depends(require_scope("conversations:read")),
    db: Session = Depends(get_db),
) -> list[Conversation]:
    """Выполнить операцию external conversations. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.organization_id == principal.organization_id)
            .order_by(Conversation.last_message_at.desc().nullslast())
            .limit(min(max(limit, 1), 500))
        ).all()
    )


@router.post("/events", response_model=MessageResponse)
def external_event(
    payload: dict[str, Any],
    principal: ServicePrincipal = Depends(require_scope("events:write")),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Выполнить операцию external event. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    event_type = str(payload.get("event_type") or "external.event")[:100]
    aggregate_type = str(payload.get("aggregate_type") or "external")[:80]
    aggregate_id = str(payload.get("aggregate_id") or principal.api_key_id)[:80]
    data_value = payload.get("data")
    data = cast(dict[str, Any], data_value) if isinstance(data_value, dict) else {}
    enqueue_event(
        db,
        organization_id=principal.organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=data,
    )
    db.commit()
    return MessageResponse(message="Событие принято")
