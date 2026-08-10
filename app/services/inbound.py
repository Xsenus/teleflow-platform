from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit import write_audit
from app.config import Settings
from app.enums import (
    BusinessConnectionStatus,
    CandidateStatus,
    ConsentStatus,
    ConversationStatus,
    InboundUpdateStatus,
    MessageAuthor,
    MessageDirection,
    SafetySeverity,
)
from app.models import (
    AIInteraction,
    AutomationPolicy,
    CandidateProfile,
    Conversation,
    ConversationMessage,
    InboundTelegramUpdate,
    TelegramBusinessConnection,
    TelegramConnection,
    new_id,
    utcnow,
)
from app.services.ai import AIService
from app.services.crypto import SecretCipher
from app.services.flows import AutomationFlowEngine
from app.services.outbox import enqueue_event
from app.services.redaction import redact_payload, redact_text
from app.services.telegram.business import BusinessBotClient

logger = logging.getLogger(__name__)

_POSITIVE_CONSENT = {"да", "согласен", "согласна", "согласие", "продолжить", "ок", "ok", "yes"}
_NEGATIVE_CONSENT = {"нет", "не согласен", "не согласна", "отказ", "no"}


class InboundService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        cipher: SecretCipher,
        *,
        worker_id: str = "inbound-worker",
    ):
        """Инициализировать InboundService with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.session_factory = session_factory
        self.settings = settings
        self.cipher = cipher
        self.worker_id = worker_id
        self.ai = AIService(settings, cipher)
        self.flows = AutomationFlowEngine(cipher)

    def accept_update(
        self,
        db: Session,
        *,
        connection: TelegramConnection,
        payload: dict[str, Any],
    ) -> tuple[InboundTelegramUpdate, bool]:
        """Выполнить операцию accept update класса InboundService. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        update_id = payload.get("update_id")
        if not isinstance(update_id, int):
            raise ValueError("Telegram update_id отсутствует или некорректен")
        existing = db.scalar(
            select(InboundTelegramUpdate).where(
                InboundTelegramUpdate.telegram_connection_id == connection.id,
                InboundTelegramUpdate.telegram_update_id == update_id,
            )
        )
        if existing:
            return existing, False
        update_type = self._detect_update_type(payload)
        item = InboundTelegramUpdate(
            id=new_id(),
            organization_id=connection.organization_id,
            telegram_connection_id=connection.id,
            telegram_update_id=update_id,
            update_type=update_type,
            status=InboundUpdateStatus.RECEIVED,
            payload_redacted=redact_payload(
                payload, preview_length=self.settings.pii_preview_length
            ),
            payload_enc="",
        )
        item.payload_enc = self.cipher.encrypt_json(
            payload, context=f"inbound-update:{item.id}:payload"
        )
        db.add(item)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(
                select(InboundTelegramUpdate).where(
                    InboundTelegramUpdate.telegram_connection_id == connection.id,
                    InboundTelegramUpdate.telegram_update_id == update_id,
                )
            )
            if existing:
                return existing, False
            raise
        return item, True

    def process_next(self) -> bool:
        """Выполнить process next класса InboundService. Операция координирует ограниченные
        побочные эффекты и возвращает детерминированный результат.
        """
        with self.session_factory() as db:
            item = db.scalar(
                select(InboundTelegramUpdate)
                .where(InboundTelegramUpdate.status == InboundUpdateStatus.RECEIVED)
                .order_by(InboundTelegramUpdate.received_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not item:
                return False
            try:
                if not item.payload_enc:
                    raise ValueError("Зашифрованное содержимое входящего update отсутствует")
                payload = self.cipher.decrypt_json(
                    item.payload_enc, context=f"inbound-update:{item.id}:payload"
                )
                if item.update_type == "business_connection":
                    self._handle_business_connection(db, item, payload["business_connection"])
                elif item.update_type == "business_message":
                    self._handle_business_message(db, item, payload["business_message"])
                elif item.update_type == "edited_business_message":
                    self._handle_edited_message(db, item, payload["edited_business_message"])
                elif item.update_type == "deleted_business_messages":
                    self._handle_deleted_messages(db, item, payload["deleted_business_messages"])
                else:
                    item.status = InboundUpdateStatus.IGNORED
                item.processed_at = utcnow()
                if item.status == InboundUpdateStatus.RECEIVED:
                    item.status = InboundUpdateStatus.PROCESSED
                db.commit()
            except Exception as exc:
                logger.exception("Failed to process Telegram update id=%s", item.id)
                db.rollback()
                failed = db.get(InboundTelegramUpdate, item.id)
                if failed:
                    failed.status = InboundUpdateStatus.FAILED
                    failed.error_code = type(exc).__name__[:100]
                    failed.error_message = str(exc)[:2000]
                    failed.processed_at = utcnow()
                    write_audit(
                        db,
                        organization_id=failed.organization_id,
                        action="inbound.update_failed",
                        entity_type="inbound_update",
                        entity_id=failed.id,
                        severity=SafetySeverity.WARNING,
                        details={"error_code": failed.error_code},
                    )
                    db.commit()
            return True

    @staticmethod
    def _detect_update_type(payload: dict[str, Any]) -> str:
        """Реализовать внутренний этап detect update type step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        for key in (
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
        ):
            if key in payload:
                return key
        return "unsupported"

    def _handle_business_connection(
        self,
        db: Session,
        update: InboundTelegramUpdate,
        data: dict[str, Any],
    ) -> TelegramBusinessConnection:
        """Реализовать внутренний этап handle business connection step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        telegram_id = str(data.get("id") or "")
        if not telegram_id:
            raise ValueError("business_connection.id отсутствует")
        connection = db.scalar(
            select(TelegramConnection).where(
                TelegramConnection.id == update.telegram_connection_id,
                TelegramConnection.organization_id == update.organization_id,
            )
        )
        if not connection:
            raise ValueError("Bot connection не найден")
        user = data.get("user") or {}
        business = db.scalar(
            select(TelegramBusinessConnection).where(
                TelegramBusinessConnection.telegram_connection_id == connection.id,
                TelegramBusinessConnection.business_connection_id == telegram_id,
            )
        )
        enabled = bool(data.get("is_enabled", data.get("can_reply", True)))
        now = utcnow()
        if not business:
            business = TelegramBusinessConnection(
                organization_id=connection.organization_id,
                telegram_connection_id=connection.id,
                business_connection_id=telegram_id,
                telegram_user_id=int(user.get("id") or 0),
                user_chat_id=int(data.get("user_chat_id") or user.get("id") or 0),
                connected_at=now if enabled else None,
            )
            db.add(business)
        business.telegram_user_id = int(user.get("id") or business.telegram_user_id or 0)
        business.user_chat_id = int(
            data.get("user_chat_id") or business.user_chat_id or business.telegram_user_id
        )
        business.username = user.get("username")
        business.first_name = user.get("first_name")
        business.last_name = user.get("last_name")
        business.rights = data.get("rights") or {
            "can_reply": data.get("can_reply"),
            "is_enabled": enabled,
        }
        business.is_enabled = enabled
        business.status = (
            BusinessConnectionStatus.ACTIVE if enabled else BusinessConnectionStatus.DISCONNECTED
        )
        business.last_update_at = now
        if not enabled:
            business.disconnected_at = now
        elif not business.connected_at:
            business.connected_at = now
        write_audit(
            db,
            organization_id=connection.organization_id,
            action="telegram.business_connection_updated",
            entity_type="telegram_business_connection",
            entity_id=business.id,
            details={"enabled": enabled, "telegram_user_id": business.telegram_user_id},
        )
        return business

    def _ensure_business_connection(
        self,
        db: Session,
        update: InboundTelegramUpdate,
        business_connection_id: str,
    ) -> TelegramBusinessConnection:
        """Реализовать внутренний этап ensure business connection step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        business = db.scalar(
            select(TelegramBusinessConnection).where(
                TelegramBusinessConnection.organization_id == update.organization_id,
                TelegramBusinessConnection.telegram_connection_id == update.telegram_connection_id,
                TelegramBusinessConnection.business_connection_id == business_connection_id,
            )
        )
        if business:
            return business
        connection = db.scalar(
            select(TelegramConnection).where(
                TelegramConnection.id == update.telegram_connection_id,
                TelegramConnection.organization_id == update.organization_id,
            )
        )
        if not connection:
            raise ValueError("Bot connection не найден")
        data = BusinessBotClient(connection, self.settings, self.cipher).get_business_connection(
            business_connection_id
        )
        return self._handle_business_connection(db, update, data)

    def _handle_business_message(
        self,
        db: Session,
        update: InboundTelegramUpdate,
        message: dict[str, Any],
    ) -> None:
        """Реализовать внутренний этап handle business message step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        business_connection_id = str(message.get("business_connection_id") or "")
        if not business_connection_id:
            raise ValueError("business_connection_id отсутствует в сообщении")
        business = self._ensure_business_connection(db, update, business_connection_id)
        if not business.is_enabled or business.status != BusinessConnectionStatus.ACTIVE:
            update.status = InboundUpdateStatus.IGNORED
            return
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id") or 0)
        if not chat_id:
            raise ValueError("chat.id отсутствует")
        sender = message.get("from") or {}
        sender_id = int(sender.get("id") or 0) or None
        is_outbound = bool(
            message.get("sender_business_bot")
            or (sender_id is not None and sender_id == business.telegram_user_id)
        )
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.organization_id == update.organization_id,
                Conversation.business_connection_id == business.id,
                Conversation.telegram_chat_id == chat_id,
            )
        )
        policy = db.scalar(
            select(AutomationPolicy)
            .where(
                AutomationPolicy.organization_id == update.organization_id,
                AutomationPolicy.telegram_connection_id == update.telegram_connection_id,
                AutomationPolicy.enabled.is_(True),
            )
            .order_by(AutomationPolicy.created_at)
            .limit(1)
        )
        now = utcnow()
        if not conversation:
            conversation = Conversation(
                organization_id=update.organization_id,
                business_connection_id=business.id,
                automation_policy_id=policy.id if policy else None,
                telegram_chat_id=chat_id,
                telegram_user_id=sender_id,
                username=sender.get("username"),
                first_name=sender.get("first_name"),
                last_name=sender.get("last_name"),
                status=ConversationStatus.NEW,
                retention_until=now + timedelta(days=self.settings.default_retention_days),
            )
            db.add(conversation)
            db.flush()
        elif policy and conversation.automation_policy_id is None:
            conversation.automation_policy_id = policy.id
        conversation.last_message_at = now
        if not is_outbound:
            conversation.last_inbound_at = now
            conversation.telegram_user_id = sender_id or conversation.telegram_user_id
            conversation.username = sender.get("username") or conversation.username
            conversation.first_name = sender.get("first_name") or conversation.first_name
            conversation.last_name = sender.get("last_name") or conversation.last_name
        else:
            conversation.last_outbound_at = now

        telegram_message_id = int(message.get("message_id") or 0) or None
        direction = MessageDirection.OUTBOUND if is_outbound else MessageDirection.INBOUND
        existing = None
        if telegram_message_id is not None:
            existing = db.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.telegram_message_id == telegram_message_id,
                    ConversationMessage.direction == direction,
                )
            )
        if existing:
            update.status = InboundUpdateStatus.IGNORED
            return
        body = str(message.get("text") or message.get("caption") or "").strip()
        if len(body) > self.settings.inbound_message_max_chars:
            body = body[: self.settings.inbound_message_max_chars]
        content_type = "text" if message.get("text") is not None else self._content_type(message)
        stored = self._store_message(
            db,
            conversation=conversation,
            telegram_message_id=telegram_message_id,
            direction=direction,
            author=MessageAuthor.BUSINESS_USER if is_outbound else MessageAuthor.CONTACT,
            body=body,
            content_type=content_type,
            reply_to_message_id=(message.get("reply_to_message") or {}).get("message_id"),
            metadata={"chat_type": chat.get("type"), "has_media": content_type != "text"},
        )
        if is_outbound:
            return
        if not policy:
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            return
        if chat.get("type") not in policy.allowed_chat_types:
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            return
        if not body:
            self._send_reply(
                db,
                business=business,
                conversation=conversation,
                policy=policy,
                text="Сейчас я могу обработать только текстовое сообщение. Передаю диалог оператору.",
                reply_to_message_id=telegram_message_id,
                author=MessageAuthor.ASSISTANT,
            )
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            return
        normalized = " ".join(body.lower().split())
        if self._matches_any(normalized, policy.stop_words):
            conversation.status = ConversationStatus.BLOCKED
            conversation.consent_status = ConsentStatus.REVOKED
            conversation.ai_enabled = False
            self._send_reply(
                db,
                business=business,
                conversation=conversation,
                policy=policy,
                text="Автоматизированные сообщения остановлены. Для удаления или экспорта данных обратитесь к оператору.",
                reply_to_message_id=telegram_message_id,
                author=MessageAuthor.ASSISTANT,
            )
            return
        if self._matches_any(normalized, policy.handoff_keywords):
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            conversation.ai_enabled = False
            self._send_reply(
                db,
                business=business,
                conversation=conversation,
                policy=policy,
                text=policy.fallback_message,
                reply_to_message_id=telegram_message_id,
                author=MessageAuthor.ASSISTANT,
            )
            return
        consent_just_granted = False
        if policy.require_consent_before_ai:
            if conversation.consent_status in {ConsentStatus.UNKNOWN, ConsentStatus.REQUESTED}:
                if normalized in _POSITIVE_CONSENT:
                    conversation.consent_status = ConsentStatus.GRANTED
                    conversation.consent_at = now
                    conversation.consent_notice_version = "v1"
                    consent_just_granted = True
                elif normalized in _NEGATIVE_CONSENT:
                    conversation.consent_status = ConsentStatus.DECLINED
                    conversation.status = ConversationStatus.HUMAN_HANDOFF
                    conversation.ai_enabled = False
                    self._send_reply(
                        db,
                        business=business,
                        conversation=conversation,
                        policy=policy,
                        text="Поняла. Автоматическая обработка отключена, диалог передан оператору.",
                        reply_to_message_id=telegram_message_id,
                        author=MessageAuthor.ASSISTANT,
                    )
                    return
                else:
                    conversation.consent_status = ConsentStatus.REQUESTED
                    conversation.status = ConversationStatus.AWAITING_CONSENT
                    self._send_reply(
                        db,
                        business=business,
                        conversation=conversation,
                        policy=policy,
                        text=policy.consent_notice,
                        reply_to_message_id=telegram_message_id,
                        author=MessageAuthor.ASSISTANT,
                    )
                    return
        if not conversation.ai_enabled or not self._policy_is_active(policy, now):
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            self._send_reply(
                db,
                business=business,
                conversation=conversation,
                policy=policy,
                text=policy.fallback_message,
                reply_to_message_id=telegram_message_id,
                author=MessageAuthor.ASSISTANT,
            )
            return
        if self._daily_reply_cap_reached(db, conversation, policy, now):
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            conversation.ai_enabled = False
            self._send_reply(
                db,
                business=business,
                conversation=conversation,
                policy=policy,
                text=policy.fallback_message,
                reply_to_message_id=telegram_message_id,
                author=MessageAuthor.ASSISTANT,
            )
            return
        if self._ai_rate_limit_reached(db, update.organization_id, now):
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            self._send_reply(
                db,
                business=business,
                conversation=conversation,
                policy=policy,
                text=policy.fallback_message,
                reply_to_message_id=telegram_message_id,
                author=MessageAuthor.ASSISTANT,
            )
            return
        flow_result = self.flows.process(
            db,
            conversation=conversation,
            flow=policy.automation_flow,
            message_text=body,
            consume_input=not consent_just_granted,
        )
        if flow_result.handled:
            stored.raw_metadata = {
                **(stored.raw_metadata or {}),
                "automation_flow_processed": True,
                "automation_flow_id": policy.automation_flow_id,
            }
            if flow_result.handoff:
                conversation.status = ConversationStatus.HUMAN_HANDOFF
                conversation.ai_enabled = False
            else:
                conversation.status = ConversationStatus.AI_ACTIVE
            if flow_result.reply_text:
                self._send_reply(
                    db,
                    business=business,
                    conversation=conversation,
                    policy=policy,
                    text=flow_result.reply_text,
                    reply_to_message_id=telegram_message_id,
                    author=MessageAuthor.ASSISTANT,
                )
            if conversation.candidate:
                conversation.candidate.consent_to_storage = (
                    conversation.consent_status == ConsentStatus.GRANTED
                )
                enqueue_event(
                    db,
                    organization_id=conversation.organization_id,
                    event_type=(
                        "candidate.ready"
                        if conversation.candidate.status == CandidateStatus.READY_FOR_REVIEW
                        else "candidate.updated"
                    ),
                    aggregate_type="candidate",
                    aggregate_id=conversation.candidate.id,
                    payload=self._candidate_payload(conversation.candidate, conversation),
                )
            return
        try:
            result = self.ai.process(
                db,
                conversation=conversation,
                source_message=stored,
                message_text=body,
                provider_config=policy.ai_provider,
            )
        except Exception:
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            conversation.ai_enabled = False
            self._send_reply(
                db,
                business=business,
                conversation=conversation,
                policy=policy,
                text=policy.fallback_message,
                reply_to_message_id=telegram_message_id,
                author=MessageAuthor.ASSISTANT,
            )
            return
        if result.handoff:
            conversation.status = ConversationStatus.HUMAN_HANDOFF
            conversation.ai_enabled = False
        else:
            conversation.status = ConversationStatus.AI_ACTIVE
        self._send_reply(
            db,
            business=business,
            conversation=conversation,
            policy=policy,
            text=result.reply_text,
            reply_to_message_id=telegram_message_id,
            author=MessageAuthor.ASSISTANT,
        )
        if conversation.candidate:
            conversation.candidate.consent_to_storage = (
                conversation.consent_status == ConsentStatus.GRANTED
            )
            enqueue_event(
                db,
                organization_id=conversation.organization_id,
                event_type=(
                    "candidate.ready"
                    if conversation.candidate.status == CandidateStatus.READY_FOR_REVIEW
                    else "candidate.updated"
                ),
                aggregate_type="candidate",
                aggregate_id=conversation.candidate.id,
                payload=self._candidate_payload(conversation.candidate, conversation),
            )

    def _handle_edited_message(
        self,
        db: Session,
        update: InboundTelegramUpdate,
        message: dict[str, Any],
    ) -> None:
        """Реализовать внутренний этап handle edited message step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        business_id = str(message.get("business_connection_id") or "")
        business = self._ensure_business_connection(db, update, business_id)
        chat_id = int((message.get("chat") or {}).get("id") or 0)
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.organization_id == update.organization_id,
                Conversation.business_connection_id == business.id,
                Conversation.telegram_chat_id == chat_id,
            )
        )
        if not conversation:
            update.status = InboundUpdateStatus.IGNORED
            return
        telegram_message_id = int(message.get("message_id") or 0)
        stored = db.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.telegram_message_id == telegram_message_id,
            )
        )
        if not stored:
            update.status = InboundUpdateStatus.IGNORED
            return
        body = str(message.get("text") or message.get("caption") or "")[
            : self.settings.inbound_message_max_chars
        ]
        stored.body_enc = self.cipher.encrypt(
            body, context=f"conversation-message:{stored.id}:body"
        )
        stored.body_preview = redact_text(body, preview_length=self.settings.pii_preview_length)
        stored.raw_metadata = {
            **stored.raw_metadata,
            "edited": True,
            "edited_at": utcnow().isoformat(),
        }

    def _handle_deleted_messages(
        self,
        db: Session,
        update: InboundTelegramUpdate,
        data: dict[str, Any],
    ) -> None:
        """Реализовать внутренний этап handle deleted messages step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        business_id = str(data.get("business_connection_id") or "")
        business = self._ensure_business_connection(db, update, business_id)
        chat_id = int(data.get("chat_id") or 0)
        ids = [int(item) for item in data.get("message_ids", []) if isinstance(item, int)]
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.organization_id == update.organization_id,
                Conversation.business_connection_id == business.id,
                Conversation.telegram_chat_id == chat_id,
            )
        )
        if not conversation or not ids:
            update.status = InboundUpdateStatus.IGNORED
            return
        messages = list(
            db.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.organization_id == update.organization_id,
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.telegram_message_id.in_(ids),
                )
            ).all()
        )
        for item in messages:
            item.body_enc = None
            item.body_preview = "[Сообщение удалено в Telegram]"
            item.raw_metadata = {**item.raw_metadata, "deleted_in_telegram": True}

    def _store_message(
        self,
        db: Session,
        *,
        conversation: Conversation,
        telegram_message_id: int | None,
        direction: MessageDirection,
        author: MessageAuthor,
        body: str,
        content_type: str,
        reply_to_message_id: int | None,
        metadata: dict[str, Any],
    ) -> ConversationMessage:
        """Реализовать внутренний этап store message step класса InboundService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        item = ConversationMessage(
            id=new_id(),
            organization_id=conversation.organization_id,
            conversation_id=conversation.id,
            telegram_message_id=telegram_message_id,
            direction=direction,
            author=author,
            body_enc=None,
            body_preview=redact_text(body, preview_length=self.settings.pii_preview_length),
            content_type=content_type,
            reply_to_message_id=reply_to_message_id,
            raw_metadata=metadata,
        )
        if body:
            item.body_enc = self.cipher.encrypt(
                body, context=f"conversation-message:{item.id}:body"
            )
        db.add(item)
        db.flush()
        return item

    def _send_reply(
        self,
        db: Session,
        *,
        business: TelegramBusinessConnection,
        conversation: Conversation,
        policy: AutomationPolicy,
        text: str,
        reply_to_message_id: int | None,
        author: MessageAuthor,
    ) -> ConversationMessage:
        """Реализовать внутренний этап send reply step класса InboundService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        if not text.strip():
            raise ValueError("Пустой ответ запрещён")
        connection = business.telegram_connection
        result = BusinessBotClient(connection, self.settings, self.cipher).send_message(
            business_connection_id=business.business_connection_id,
            chat_id=conversation.telegram_chat_id,
            text=text[:4000],
            reply_to_message_id=reply_to_message_id,
        )
        item = self._store_message(
            db,
            conversation=conversation,
            telegram_message_id=int(result.get("message_id") or 0) or None,
            direction=MessageDirection.OUTBOUND,
            author=author,
            body=text[:4000],
            content_type="text",
            reply_to_message_id=reply_to_message_id,
            metadata={"business_connection_id": business.business_connection_id},
        )
        conversation.last_outbound_at = utcnow()
        conversation.last_message_at = conversation.last_outbound_at
        return item

    def send_operator_reply(
        self,
        db: Session,
        *,
        conversation: Conversation,
        text: str,
    ) -> ConversationMessage:
        """Выполнить операцию send operator reply класса InboundService. Аргументы интерпретируются
        в контексте модуля, результат возвращается вызывающему коду.
        """
        policy = conversation.policy
        if not policy:
            raise ValueError("Для диалога не найдена automation policy")
        message = self._send_reply(
            db,
            business=conversation.business_connection,
            conversation=conversation,
            policy=policy,
            text=text,
            reply_to_message_id=None,
            author=MessageAuthor.OPERATOR,
        )
        conversation.status = ConversationStatus.HUMAN_HANDOFF
        conversation.ai_enabled = False
        return message

    @staticmethod
    def _matches_any(text: str, values: list[str]) -> bool:
        """Реализовать внутренний этап matches any step класса InboundService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        normalized = text.strip().lower()
        return any(value.strip().lower() in normalized for value in values if value.strip())

    @staticmethod
    def _content_type(message: dict[str, Any]) -> str:
        """Реализовать внутренний этап content type step класса InboundService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        for key in (
            "photo",
            "video",
            "document",
            "voice",
            "audio",
            "sticker",
            "location",
            "contact",
        ):
            if key in message:
                return key
        return "unsupported"

    @staticmethod
    def _candidate_payload(
        candidate: CandidateProfile, conversation: Conversation
    ) -> dict[str, Any]:
        """Реализовать внутренний этап candidate payload step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        return {
            "candidate_id": candidate.id,
            "conversation_id": conversation.id,
            "full_name": candidate.full_name,
            "city": candidate.city,
            "age": candidate.age,
            "experience": candidate.experience,
            "schedule": candidate.schedule,
            "vacancy_key": candidate.vacancy_key,
            "status": candidate.status.value,
            "summary": candidate.summary,
            "consent_to_storage": candidate.consent_to_storage,
            "telegram_username": conversation.username,
        }

    def _daily_reply_cap_reached(
        self,
        db: Session,
        conversation: Conversation,
        policy: AutomationPolicy,
        now: datetime,
    ) -> bool:
        """Реализовать внутренний этап daily reply cap reached step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        count = (
            db.scalar(
                select(func.count(ConversationMessage.id)).where(
                    ConversationMessage.organization_id == conversation.organization_id,
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.direction == MessageDirection.OUTBOUND,
                    ConversationMessage.author == MessageAuthor.ASSISTANT,
                    ConversationMessage.created_at >= day_start,
                )
            )
            or 0
        )
        return count >= policy.max_auto_replies_per_day

    def _ai_rate_limit_reached(self, db: Session, organization_id: str, now: datetime) -> bool:
        """Реализовать внутренний этап ai rate limit reached step класса InboundService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        count = (
            db.scalar(
                select(func.count(AIInteraction.id)).where(
                    AIInteraction.organization_id == organization_id,
                    AIInteraction.created_at >= now - timedelta(minutes=1),
                )
            )
            or 0
        )
        return count >= self.settings.ai_request_rate_per_minute

    @staticmethod
    def _policy_is_active(policy: AutomationPolicy, now: datetime) -> bool:
        """Реализовать внутренний этап policy is active step класса InboundService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        if not policy.active_hours:
            return True
        try:
            local = now.astimezone(ZoneInfo(policy.timezone_name))
        except Exception:
            local = now
        day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][local.weekday()]
        windows = policy.active_hours.get(day_key) or policy.active_hours.get(str(local.weekday()))
        if not windows:
            return False
        for window in windows:
            try:
                start_raw, end_raw = window
                start = time.fromisoformat(str(start_raw))
                end = time.fromisoformat(str(end_raw))
                if start <= local.time() <= end:
                    return True
            except (TypeError, ValueError):
                continue
        return False
