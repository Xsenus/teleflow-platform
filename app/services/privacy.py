from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import write_audit
from app.config import Settings
from app.enums import (
    PrivacyRequestStatus,
    PrivacyRequestType,
    RecoveryBackupStatus,
    SafetySeverity,
)
from app.models import (
    AIInteraction,
    Conversation,
    ConversationMessage,
    InboundTelegramUpdate,
    PrivacyRequest,
    RecoveryBackupEvidence,
    utcnow,
)
from app.security import aware_utc
from app.services.crypto import SecretCipher
from app.services.storage import StorageService
from app.services.support_bundle import expire_support_bundles

logger = logging.getLogger(__name__)


class PrivacyService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        cipher: SecretCipher,
        storage: StorageService,
    ):
        """Инициализировать PrivacyService with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.session_factory = session_factory
        self.settings = settings
        self.cipher = cipher
        self.storage = storage

    def process_next(self) -> bool:
        """Выполнить process next класса PrivacyService. Операция координирует ограниченные
        побочные эффекты и возвращает детерминированный результат.
        """
        with self.session_factory() as db:
            item = db.scalar(
                select(PrivacyRequest)
                .where(PrivacyRequest.status == PrivacyRequestStatus.PENDING)
                .order_by(PrivacyRequest.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not item:
                return False
            self.process(db, item)
            return True

    def process(self, db: Session, item: PrivacyRequest) -> None:
        """Выполнить операцию process класса PrivacyService. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        item.status = PrivacyRequestStatus.PROCESSING
        db.commit()
        try:
            conversations = self._find_conversations(db, item)
            if item.request_type == PrivacyRequestType.EXPORT:
                self._export(db, item, conversations)
            else:
                self._delete(db, item, conversations)
            item.status = PrivacyRequestStatus.COMPLETED
            item.processed_at = utcnow()
            item.details = {
                **(item.details or {}),
                "conversation_count": len(conversations),
            }
            write_audit(
                db,
                organization_id=item.organization_id,
                action=f"privacy.{item.request_type.value}_completed",
                entity_type="privacy_request",
                entity_id=item.id,
                details={"conversation_count": len(conversations)},
            )
            db.commit()
        except Exception as exc:
            logger.exception("Privacy request failed id=%s", item.id)
            db.rollback()
            failed = db.get(PrivacyRequest, item.id)
            if failed:
                failed.status = PrivacyRequestStatus.FAILED
                failed.error_message = str(exc)[:2000]
                failed.processed_at = utcnow()
                write_audit(
                    db,
                    organization_id=failed.organization_id,
                    action="privacy.request_failed",
                    entity_type="privacy_request",
                    entity_id=failed.id,
                    severity=SafetySeverity.WARNING,
                    details={"error": type(exc).__name__},
                )
                db.commit()
            raise

    def read_export(self, item: PrivacyRequest) -> bytes:
        """Прочитать export класса PrivacyService. Значение возвращается без несвязанных изменений
        состояния.
        """
        if item.status != PrivacyRequestStatus.COMPLETED or not item.output_relative_path:
            raise FileNotFoundError("Privacy export не готов")
        processed_at = aware_utc(item.processed_at)
        expires_at = (
            processed_at + timedelta(hours=self.settings.privacy_export_ttl_hours)
            if processed_at
            else None
        )
        if expires_at and expires_at < utcnow():
            self.storage.delete(item.output_relative_path)
            raise FileNotFoundError("Privacy export истёк")
        encrypted = self.storage.read_bytes(item.output_relative_path).decode("utf-8")
        raw = self.cipher.decrypt(encrypted, context=f"privacy-export:{item.id}")
        return raw.encode("utf-8")

    def _find_conversations(self, db: Session, item: PrivacyRequest) -> list[Conversation]:
        """Реализовать внутренний этап find conversations step класса PrivacyService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        stmt = select(Conversation).where(Conversation.organization_id == item.organization_id)
        conditions = []
        if item.conversation_id:
            conditions.append(Conversation.id == item.conversation_id)
        if item.telegram_user_id:
            conditions.append(Conversation.telegram_user_id == item.telegram_user_id)
        if item.telegram_chat_id:
            conditions.append(Conversation.telegram_chat_id == item.telegram_chat_id)
        if not conditions:
            return []
        return list(db.scalars(stmt.where(or_(*conditions))).all())

    def _export(
        self,
        db: Session,
        item: PrivacyRequest,
        conversations: list[Conversation],
    ) -> None:
        """Реализовать внутренний этап export step класса PrivacyService. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        payload: dict[str, Any] = {
            "request_id": item.id,
            "generated_at": utcnow().isoformat(),
            "organization_id": item.organization_id,
            "conversations": [],
        }
        for conversation in conversations:
            messages = list(
                db.scalars(
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == conversation.id)
                    .order_by(ConversationMessage.created_at)
                ).all()
            )
            candidate = conversation.candidate
            candidate_data = None
            if candidate:
                candidate_data = {
                    "id": candidate.id,
                    "full_name": candidate.full_name,
                    "city": candidate.city,
                    "age": candidate.age,
                    "experience": candidate.experience,
                    "schedule": candidate.schedule,
                    "vacancy_key": candidate.vacancy_key,
                    "status": candidate.status.value,
                    "summary": candidate.summary,
                    "phone": self._decrypt_optional(
                        candidate.phone_enc, f"candidate:{candidate.id}:phone"
                    ),
                    "email": self._decrypt_optional(
                        candidate.email_enc, f"candidate:{candidate.id}:email"
                    ),
                    "structured_data": candidate.structured_data,
                }
            payload["conversations"].append(
                {
                    "id": conversation.id,
                    "telegram_chat_id": conversation.telegram_chat_id,
                    "telegram_user_id": conversation.telegram_user_id,
                    "username": conversation.username,
                    "first_name": conversation.first_name,
                    "last_name": conversation.last_name,
                    "status": conversation.status.value,
                    "consent_status": conversation.consent_status.value,
                    "vacancy_key": conversation.vacancy_key,
                    "created_at": conversation.created_at.isoformat(),
                    "messages": [
                        {
                            "id": message.id,
                            "telegram_message_id": message.telegram_message_id,
                            "direction": message.direction.value,
                            "author": message.author.value,
                            "body": self._decrypt_optional(
                                message.body_enc,
                                f"conversation-message:{message.id}:body",
                            ),
                            "content_type": message.content_type,
                            "created_at": message.created_at.isoformat(),
                        }
                        for message in messages
                    ],
                    "candidate": candidate_data,
                }
            )
        raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if len(raw.encode("utf-8")) > self.settings.max_export_bytes:
            raise ValueError("Privacy export превышает системный лимит")
        encrypted = self.cipher.encrypt(raw, context=f"privacy-export:{item.id}")
        key = f"exports/privacy/{item.organization_id}/{item.id}.json.enc"
        self.storage.put_bytes(
            key, encrypted.encode("utf-8"), content_type="application/octet-stream"
        )
        item.output_relative_path = key
        item.details = {
            **(item.details or {}),
            "expires_at": (
                utcnow() + timedelta(hours=self.settings.privacy_export_ttl_hours)
            ).isoformat(),
        }

    def _delete(
        self,
        db: Session,
        item: PrivacyRequest,
        conversations: list[Conversation],
    ) -> None:
        """Реализовать внутренний этап delete step класса PrivacyService. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        ids = [conversation.id for conversation in conversations]
        if not ids:
            return
        # Scrub raw Telegram updates that mention the same chat/user before removing entities.
        updates = list(
            db.scalars(
                select(InboundTelegramUpdate).where(
                    InboundTelegramUpdate.organization_id == item.organization_id,
                    InboundTelegramUpdate.payload_enc.is_not(None),
                )
            ).all()
        )
        chat_ids = {conversation.telegram_chat_id for conversation in conversations}
        user_ids = {
            conversation.telegram_user_id
            for conversation in conversations
            if conversation.telegram_user_id
        }
        for update in updates:
            if not update.payload_enc:
                continue
            try:
                payload = self.cipher.decrypt_json(
                    update.payload_enc, context=f"inbound-update:{update.id}:payload"
                )
            except Exception:
                continue
            serialized = json.dumps(payload, ensure_ascii=False)
            if any(str(value) in serialized for value in chat_ids | user_ids):
                update.payload_enc = None
                update.payload_redacted = {"privacy_deleted": True}
        db.execute(delete(AIInteraction).where(AIInteraction.conversation_id.in_(ids)))
        for conversation in conversations:
            db.delete(conversation)
        item.conversation_id = None
        item.details = {**(item.details or {}), "deleted_conversation_ids": ids}
        db.flush()

    def _decrypt_optional(self, value: str | None, context: str) -> str | None:
        """Реализовать внутренний этап decrypt optional step класса PrivacyService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        if not value:
            return None
        try:
            return self.cipher.decrypt(value, context=context)
        except Exception:
            return "[DECRYPTION_FAILED]"


class RetentionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        storage: StorageService,
    ):
        """Инициализировать RetentionService with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.session_factory = session_factory
        self.settings = settings
        self.storage = storage

    def run(self) -> dict[str, int]:
        """Выполнить операцию run класса RetentionService. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        now = utcnow()
        stats = {
            "conversations_scrubbed": 0,
            "updates_scrubbed": 0,
            "exports_deleted": 0,
            "support_bundles_deleted": 0,
            "recovery_evidence_expired": 0,
        }
        with self.session_factory() as db:
            conversations = list(
                db.scalars(
                    select(Conversation).where(
                        Conversation.retention_until.is_not(None),
                        Conversation.retention_until < now,
                    )
                ).all()
            )
            for conversation in conversations:
                for message in conversation.messages:
                    message.body_enc = None
                    message.body_preview = "[Удалено по сроку хранения]"
                if conversation.candidate:
                    conversation.candidate.phone_enc = None
                    conversation.candidate.email_enc = None
                    conversation.candidate.structured_data = {}
                conversation.first_name = None
                conversation.last_name = None
                conversation.username = None
                conversation.metadata_json = {"retention_scrubbed_at": now.isoformat()}
                conversation.retention_until = None
                stats["conversations_scrubbed"] += 1
            cutoff = now - timedelta(days=self.settings.inbound_raw_retention_days)
            updates = list(
                db.scalars(
                    select(InboundTelegramUpdate).where(
                        InboundTelegramUpdate.received_at < cutoff,
                        InboundTelegramUpdate.payload_enc.is_not(None),
                    )
                ).all()
            )
            for update in updates:
                update.payload_enc = None
                stats["updates_scrubbed"] += 1
            expired_exports = list(
                db.scalars(
                    select(PrivacyRequest).where(
                        PrivacyRequest.request_type == PrivacyRequestType.EXPORT,
                        PrivacyRequest.status == PrivacyRequestStatus.COMPLETED,
                        PrivacyRequest.processed_at
                        < now - timedelta(hours=self.settings.privacy_export_ttl_hours),
                        PrivacyRequest.output_relative_path.is_not(None),
                    )
                ).all()
            )
            for request in expired_exports:
                if not request.output_relative_path:
                    continue
                try:
                    self.storage.delete(request.output_relative_path)
                except Exception:
                    pass
                request.output_relative_path = None
                stats["exports_deleted"] += 1
            stats["support_bundles_deleted"] = expire_support_bundles(
                db, organization_id=None, storage=self.storage, now=now
            )
            recovery_candidates = list(
                db.scalars(
                    select(RecoveryBackupEvidence).where(
                        RecoveryBackupEvidence.status.in_(
                            [RecoveryBackupStatus.REGISTERED, RecoveryBackupStatus.VERIFIED]
                        ),
                        RecoveryBackupEvidence.expires_at.is_not(None),
                    )
                ).all()
            )
            expired_recovery: list[RecoveryBackupEvidence] = []
            for evidence in recovery_candidates:
                expires_at = aware_utc(evidence.expires_at)
                if expires_at is not None and expires_at <= now:
                    expired_recovery.append(evidence)
            for evidence in expired_recovery:
                evidence.status = RecoveryBackupStatus.EXPIRED
                evidence.error_message = (
                    evidence.error_message
                    or "Recovery evidence истёк по политике хранения metadata."
                )
                stats["recovery_evidence_expired"] += 1
            db.commit()
        return stats
