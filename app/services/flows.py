from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.enums import CandidateStatus, ConsentStatus
from app.models import AutomationFlow, CandidateProfile, Conversation, utcnow
from app.schemas import AutomationFlowDefinition, AutomationFlowNode
from app.services.crypto import SecretCipher

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s().-]{5,39}$")


@dataclass(slots=True)
class FlowResult:
    handled: bool = False
    reply_text: str | None = None
    handoff: bool = False
    completed: bool = False
    candidate_changed: bool = False


class AutomationFlowEngine:
    """Executes a validated, deterministic candidate intake flow.

    The engine never sends Telegram messages itself. It returns at most one combined
    reply to the inbound service, which keeps rate limits and auditing centralized.
    Raw phone/email values are stored only in encrypted candidate fields and are not
    duplicated in the JSON flow state.
    """

    def __init__(self, cipher: SecretCipher):
        """Инициализировать AutomationFlowEngine with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.cipher = cipher

    def process(
        self,
        db: Session,
        *,
        conversation: Conversation,
        flow: AutomationFlow | None,
        message_text: str,
        consume_input: bool = True,
    ) -> FlowResult:
        """Выполнить операцию process класса AutomationFlowEngine. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if not flow or not flow.is_active or conversation.flow_completed_at is not None:
            return FlowResult()

        definition = AutomationFlowDefinition.model_validate(flow.definition)
        by_id = {node.id: node for node in definition.nodes}
        previous_state = dict(conversation.flow_state_json or {})
        same_revision = (
            previous_state.get("flow_id") == flow.id
            and previous_state.get("revision") == flow.revision
            and conversation.flow_node_id in by_id
        )
        if same_revision:
            state = previous_state
            node_id = conversation.flow_node_id or definition.start_node_id
            can_consume = consume_input
        else:
            state = {
                "flow_id": flow.id,
                "revision": flow.revision,
                "awaiting_node_id": None,
                "answered_fields": [],
                "visited_nodes": [],
                "started_at": utcnow().isoformat(),
            }
            node_id = definition.start_node_id
            can_consume = False
            conversation.flow_completed_at = None

        replies: list[str] = []
        candidate_changed = False
        # Graph validation rejects cycles, but the hard limit protects against a
        # malformed database value or a future schema regression.
        for _ in range(60):
            node = by_id.get(node_id)
            if node is None:
                return self._terminal_error(conversation, state, replies)
            visited = list(state.get("visited_nodes") or [])
            visited.append(node.id)
            state["visited_nodes"] = visited[-100:]
            conversation.flow_node_id = node.id

            if node.type == "message":
                replies.append(node.text or "")
                node_id = node.next_node_id or ""
                continue

            if node.type in {"question", "choice"}:
                candidate = self._ensure_candidate(db, conversation)
                if node.skip_if_present and self._field_is_present(candidate, node.field):
                    existing_next = self._next_after_existing(node, candidate)
                    if existing_next is not None:
                        state["awaiting_node_id"] = None
                        node_id = existing_next
                        continue

                awaiting = state.get("awaiting_node_id") == node.id
                if awaiting and can_consume:
                    error, next_node = self._capture(candidate, node, message_text)
                    if error:
                        replies.extend([error, node.text or ""])
                        self._save_state(conversation, state, node.id)
                        return FlowResult(
                            handled=True,
                            reply_text=self._join_replies(replies),
                            candidate_changed=False,
                        )
                    candidate_changed = True
                    answered = list(state.get("answered_fields") or [])
                    if node.field and node.field not in answered:
                        answered.append(node.field)
                    state["answered_fields"] = answered
                    state["awaiting_node_id"] = None
                    state["last_answered_at"] = utcnow().isoformat()
                    can_consume = False
                    node_id = next_node
                    continue

                replies.append(node.text or "")
                if node.type == "choice":
                    replies.append(
                        "Варианты: " + ", ".join(option.label for option in node.options)
                    )
                state["awaiting_node_id"] = node.id
                self._save_state(conversation, state, node.id)
                if candidate.status == CandidateStatus.NEW:
                    candidate.status = CandidateStatus.QUALIFYING
                return FlowResult(
                    handled=True,
                    reply_text=self._join_replies(replies),
                    candidate_changed=candidate_changed,
                )

            if node.type == "handoff":
                replies.append(node.text or "")
                self._complete(conversation, state, node.id, mode="handoff")
                return FlowResult(
                    handled=True,
                    reply_text=self._join_replies(replies),
                    handoff=True,
                    completed=True,
                    candidate_changed=candidate_changed,
                )

            if node.type == "end":
                replies.append(node.text or "")
                candidate = self._ensure_candidate(db, conversation)
                candidate.status = CandidateStatus.READY_FOR_REVIEW
                candidate.consent_to_storage = conversation.consent_status == ConsentStatus.GRANTED
                candidate_changed = True
                self._complete(conversation, state, node.id, mode=node.completion_mode)
                return FlowResult(
                    handled=True,
                    reply_text=self._join_replies(replies),
                    handoff=node.completion_mode == "handoff",
                    completed=True,
                    candidate_changed=True,
                )

        return self._terminal_error(conversation, state, replies)

    def _ensure_candidate(self, db: Session, conversation: Conversation) -> CandidateProfile:
        """Реализовать внутренний этап ensure candidate step класса AutomationFlowEngine.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        candidate = conversation.candidate
        if candidate is None:
            candidate = CandidateProfile(
                organization_id=conversation.organization_id,
                conversation=conversation,
                vacancy_key=conversation.vacancy_key,
                consent_to_storage=conversation.consent_status == ConsentStatus.GRANTED,
            )
            db.add(candidate)
            db.flush()
        return candidate

    def _capture(
        self,
        candidate: CandidateProfile,
        node: AutomationFlowNode,
        raw_value: str,
    ) -> tuple[str | None, str]:
        """Реализовать внутренний этап capture step класса AutomationFlowEngine. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        value = " ".join(raw_value.strip().split())
        if node.type == "choice":
            normalized = value.lower()
            selected = next(
                (
                    option
                    for option in node.options
                    if normalized
                    in {
                        " ".join(option.label.lower().split()),
                        *(" ".join(alias.lower().split()) for alias in option.aliases),
                    }
                ),
                None,
            )
            if selected is None:
                return "Не удалось распознать вариант. Выберите один из перечисленных.", node.id
            value = selected.label
            next_node = selected.next_node_id
        else:
            next_node = node.next_node_id or ""

        if not value:
            return "Ответ не должен быть пустым.", node.id
        field = node.field
        if field == "age" or node.validation == "age":
            try:
                age = int(value)
            except ValueError:
                return "Укажите возраст целым числом.", node.id
            if not 14 <= age <= 120:
                return "Возраст должен быть от 14 до 120 лет.", node.id
            candidate.age = age
        elif field == "phone" or node.validation == "phone":
            if not _PHONE_RE.fullmatch(value):
                return "Укажите телефон в международном или обычном числовом формате.", node.id
            candidate.phone_enc = self.cipher.encrypt(
                value[:80], context=f"candidate:{candidate.id}:phone"
            )
        elif field == "email" or node.validation == "email":
            if not _EMAIL_RE.fullmatch(value) or len(value) > 320:
                return "Укажите корректный адрес электронной почты.", node.id
            candidate.email_enc = self.cipher.encrypt(
                value.lower(), context=f"candidate:{candidate.id}:email"
            )
        elif field in {"full_name", "city", "experience", "schedule", "vacancy_key"}:
            limits = {
                "full_name": 180,
                "city": 120,
                "experience": 4000,
                "schedule": 250,
                "vacancy_key": 120,
            }
            setattr(candidate, field, value[: limits[field]])
        else:
            return "Поле анкеты не поддерживается.", node.id

        if field == "vacancy_key":
            candidate.conversation.vacancy_key = candidate.vacancy_key
        if candidate.status == CandidateStatus.NEW:
            candidate.status = CandidateStatus.QUALIFYING
        candidate.structured_data = {
            **(candidate.structured_data or {}),
            "last_flow_update_at": utcnow().isoformat(),
        }
        return None, next_node

    @staticmethod
    def _field_is_present(candidate: CandidateProfile, field: str | None) -> bool:
        """Реализовать внутренний этап field is present step класса AutomationFlowEngine.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        if field == "phone":
            return bool(candidate.phone_enc)
        if field == "email":
            return bool(candidate.email_enc)
        return bool(field and getattr(candidate, field, None) not in {None, ""})

    @staticmethod
    def _next_after_existing(node: AutomationFlowNode, candidate: CandidateProfile) -> str | None:
        """Реализовать внутренний этап next after existing step класса AutomationFlowEngine.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        if node.type != "choice":
            return node.next_node_id or ""
        value = getattr(candidate, node.field or "", None)
        normalized = " ".join(str(value or "").lower().split())
        for option in node.options:
            accepted = {
                " ".join(option.label.lower().split()),
                *(" ".join(alias.lower().split()) for alias in option.aliases),
            }
            if normalized in accepted:
                return option.next_node_id
        # A manually populated value may not correspond to a branch. Ask again
        # rather than guessing a route.
        return None

    @staticmethod
    def _save_state(conversation: Conversation, state: dict[str, Any], node_id: str) -> None:
        """Реализовать внутренний этап save state step класса AutomationFlowEngine. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        conversation.flow_node_id = node_id
        conversation.flow_state_json = dict(state)

    @staticmethod
    def _complete(
        conversation: Conversation,
        state: dict[str, Any],
        node_id: str,
        *,
        mode: str,
    ) -> None:
        """Реализовать внутренний этап complete step класса AutomationFlowEngine. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        now = utcnow()
        state["awaiting_node_id"] = None
        state["completed_at"] = now.isoformat()
        state["completion_mode"] = mode
        conversation.flow_node_id = node_id
        conversation.flow_state_json = dict(state)
        conversation.flow_completed_at = now

    @staticmethod
    def _join_replies(replies: list[str]) -> str | None:
        """Реализовать внутренний этап join replies step класса AutomationFlowEngine.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        cleaned = [item.strip() for item in replies if item and item.strip()]
        return "\n\n".join(cleaned) if cleaned else None

    def _terminal_error(
        self,
        conversation: Conversation,
        state: dict[str, Any],
        replies: list[str],
    ) -> FlowResult:
        """Реализовать внутренний этап terminal error step класса AutomationFlowEngine.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        replies.append("Сценарий остановлен из-за ошибки конфигурации. Передаю диалог оператору.")
        self._complete(conversation, state, conversation.flow_node_id or "invalid", mode="handoff")
        return FlowResult(
            handled=True,
            reply_text=self._join_replies(replies),
            handoff=True,
            completed=True,
        )
