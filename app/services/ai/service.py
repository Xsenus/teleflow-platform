from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    AIInteractionStatus,
    AIProviderKind,
    CandidateStatus,
    MessageDirection,
)
from app.models import (
    AIInteraction,
    AIProviderConfig,
    CandidateProfile,
    Conversation,
    ConversationMessage,
    KnowledgeBaseArticle,
    utcnow,
)
from app.services.ai.base import AIProvider, AIResult
from app.services.ai.openai_compatible import OpenAICompatibleProvider
from app.services.ai.rule_based import RuleBasedProvider
from app.services.crypto import SecretCipher
from app.services.redaction import (
    detect_prompt_injection,
    minimize_for_ai,
    redact_text,
    stable_hash,
)
from app.services.url_security import validate_outbound_url

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, settings: Settings, cipher: SecretCipher):
        """Инициализировать AIService with its explicit dependencies. Сохраняется только состояние,
        необходимое последующим операциям.
        """
        self.settings = settings
        self.cipher = cipher

    def process(
        self,
        db: Session,
        *,
        conversation: Conversation,
        source_message: ConversationMessage,
        message_text: str,
        provider_config: AIProviderConfig | None,
    ) -> AIResult:
        """Выполнить операцию process класса AIService. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        provider = self._build_provider(provider_config)
        history = self._history(db, conversation)
        knowledge = self._knowledge(db, conversation)
        candidate = conversation.candidate
        if candidate is None:
            candidate = CandidateProfile(
                organization_id=conversation.organization_id,
                conversation=conversation,
                vacancy_key=conversation.vacancy_key,
            )
            db.add(candidate)
            db.flush()
        candidate_state = self._candidate_state(candidate)
        safe_message = minimize_for_ai(message_text, max_chars=self.settings.ai_max_context_chars)
        request_material = json.dumps(
            {
                "provider": provider.name,
                "model": provider.model_name,
                "conversation": conversation.id,
                "message": safe_message,
                "history": history,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        interaction = AIInteraction(
            organization_id=conversation.organization_id,
            conversation_id=conversation.id,
            source_message_id=source_message.id,
            provider_config_id=provider_config.id if provider_config else None,
            provider_kind=provider.name,
            model_name=provider.model_name,
            request_hash=stable_hash(request_material),
            prompt_preview=redact_text(
                safe_message, preview_length=self.settings.pii_preview_length
            ),
            response_preview="",
            status=AIInteractionStatus.FAILED,
            safety_flags={},
        )
        db.add(interaction)
        started = time.perf_counter()
        try:
            result = provider.generate(
                message=safe_message,
                history=history,
                knowledge=knowledge,
                candidate=candidate_state,
                system_prompt=(provider_config.system_prompt if provider_config else ""),
            )
            injection = detect_prompt_injection(safe_message)
            if injection:
                result.safety_flags.setdefault("prompt_injection_detected", True)
            self._apply_candidate_updates(candidate, result.candidate_updates)
            if result.vacancy_key:
                conversation.vacancy_key = result.vacancy_key
                candidate.vacancy_key = result.vacancy_key
            interaction.status = AIInteractionStatus.SUCCEEDED
            interaction.response_preview = redact_text(
                result.reply_text, preview_length=self.settings.pii_preview_length
            )
            interaction.input_tokens = result.input_tokens
            interaction.output_tokens = result.output_tokens
            interaction.safety_flags = result.safety_flags
            return result
        except Exception as exc:
            logger.exception("AI processing failed conversation=%s", conversation.id)
            interaction.status = AIInteractionStatus.FAILED
            interaction.error_message = str(exc)[:2000]
            raise
        finally:
            interaction.latency_ms = int((time.perf_counter() - started) * 1000)
            source_message.ai_processed = True

    def _build_provider(self, config: AIProviderConfig | None) -> AIProvider:
        """Реализовать внутренний этап build provider step класса AIService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        if config is None or config.kind == AIProviderKind.RULE_BASED:
            return RuleBasedProvider()
        if not config.enabled:
            return RuleBasedProvider()
        if config.kind.value not in self.settings.ai_provider_allowlist_values:
            raise ValueError("AI-провайдер запрещён системным allowlist")
        if config.kind == AIProviderKind.OPENAI_COMPATIBLE:
            if not config.api_key_enc or not config.base_url or not config.model_name:
                raise ValueError("AI-провайдер настроен не полностью")
            validate_outbound_url(
                config.base_url,
                allow_private=self.settings.allow_private_integration_urls,
                require_https=self.settings.is_production,
            )
            api_key = self.cipher.decrypt(
                config.api_key_enc, context=f"ai-provider:{config.id}:api-key"
            )
            return OpenAICompatibleProvider(
                base_url=config.base_url,
                api_key=api_key,
                model_name=config.model_name,
                timeout_seconds=config.timeout_seconds,
                max_output_tokens=config.max_output_tokens,
                temperature=config.temperature_milli / 1000,
                allowed_models=config.allowed_models,
            )
        return RuleBasedProvider()

    def _history(self, db: Session, conversation: Conversation) -> list[dict[str, str]]:
        """Реализовать внутренний этап history step класса AIService. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        items = list(
            db.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.organization_id == conversation.organization_id,
                    ConversationMessage.conversation_id == conversation.id,
                )
                .order_by(ConversationMessage.created_at.desc())
                .limit(self.settings.ai_max_context_messages)
            ).all()
        )
        result: list[dict[str, str]] = []
        chars = 0
        for item in reversed(items):
            if not item.body_enc:
                continue
            try:
                body = self.cipher.decrypt(
                    item.body_enc, context=f"conversation-message:{item.id}:body"
                )
            except Exception:
                continue
            role = "user" if item.direction == MessageDirection.INBOUND else "assistant"
            chars += len(body)
            if chars > self.settings.ai_max_context_chars:
                break
            result.append({"role": role, "content": body})
        return result

    def _knowledge(self, db: Session, conversation: Conversation) -> list[dict[str, Any]]:
        """Реализовать внутренний этап knowledge step класса AIService. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        stmt = select(KnowledgeBaseArticle).where(
            KnowledgeBaseArticle.organization_id == conversation.organization_id,
            KnowledgeBaseArticle.is_active.is_(True),
        )
        if conversation.vacancy_key:
            stmt = stmt.where(
                (KnowledgeBaseArticle.vacancy_key == conversation.vacancy_key)
                | (KnowledgeBaseArticle.vacancy_key.is_(None))
            )
        articles = list(
            db.scalars(stmt.order_by(KnowledgeBaseArticle.updated_at.desc()).limit(30)).all()
        )
        return [
            {"title": item.title, "content": item.content, "tags": item.tags} for item in articles
        ]

    @staticmethod
    def _candidate_state(candidate: CandidateProfile) -> dict[str, Any]:
        """Реализовать внутренний этап candidate state step класса AIService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        return {
            "full_name": candidate.full_name,
            "city": candidate.city,
            "age": candidate.age,
            "experience": candidate.experience,
            "schedule": candidate.schedule,
            "vacancy_key": candidate.vacancy_key,
            "status": candidate.status.value,
            **(candidate.structured_data or {}),
        }

    def _apply_candidate_updates(
        self, candidate: CandidateProfile, updates: dict[str, Any]
    ) -> None:
        """Реализовать внутренний этап apply candidate updates step класса AIService.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        allowed = {"full_name", "city", "age", "experience", "schedule", "vacancy_key", "summary"}
        for key in allowed:
            value = updates.get(key)
            if value is not None:
                if key == "age":
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        continue
                    if not 14 <= value <= 120:
                        continue
                setattr(candidate, key, value)
        if updates.get("phone"):
            candidate.phone_enc = self.cipher.encrypt(
                str(updates["phone"])[:80], context=f"candidate:{candidate.id}:phone"
            )
        if updates.get("email"):
            candidate.email_enc = self.cipher.encrypt(
                str(updates["email"])[:320], context=f"candidate:{candidate.id}:email"
            )
        status = updates.get("status")
        if status:
            try:
                candidate.status = CandidateStatus(status)
            except ValueError:
                pass
        elif any([candidate.full_name, candidate.city, candidate.experience]):
            candidate.status = CandidateStatus.QUALIFYING
        candidate.structured_data = {
            **(candidate.structured_data or {}),
            "last_ai_update_at": utcnow().isoformat(),
        }
