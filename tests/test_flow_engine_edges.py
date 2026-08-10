from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.enums import CandidateStatus, ConsentStatus
from app.schemas import AutomationFlowNode
from app.services.flows import AutomationFlowEngine


class RecordingCipher:
    """Шифровать тестовые значения детерминированно и сохранять контекст вызова."""

    def __init__(self) -> None:
        """Подготовить журнал операций шифрования."""

        self.calls: list[tuple[str, str]] = []

    def encrypt(self, value: str, *, context: str) -> str:
        """Вернуть безопасный маркер вместо открытого тестового значения."""

        self.calls.append((value, context))
        return f"encrypted:{context}"


def _candidate(**overrides):  # type: ignore[no-untyped-def]
    """Создать минимальный объект анкеты со всеми полями, используемыми flow engine."""

    values = {
        "id": "candidate-id",
        "status": CandidateStatus.NEW,
        "phone_enc": None,
        "email_enc": None,
        "age": None,
        "full_name": None,
        "city": None,
        "experience": None,
        "schedule": None,
        "vacancy_key": None,
        "structured_data": {},
        "conversation": SimpleNamespace(vacancy_key=None),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _conversation(candidate=None):  # type: ignore[no-untyped-def]
    """Создать состояние диалога, достаточное для детерминированного исполнения flow."""

    return SimpleNamespace(
        organization_id="organization-id",
        vacancy_key=None,
        consent_status=ConsentStatus.GRANTED,
        candidate=candidate,
        flow_completed_at=None,
        flow_state_json={},
        flow_node_id=None,
    )


def _flow(definition: dict, *, revision: int = 1):
    """Создать активный flow с заданным сериализованным графом."""

    return SimpleNamespace(id="flow-id", revision=revision, is_active=True, definition=definition)


@pytest.mark.parametrize(
    ("node_payload", "raw_value", "expected"),
    [
        (
            {
                "id": "choice",
                "type": "choice",
                "text": "График?",
                "field": "schedule",
                "options": [
                    {"label": "Полный", "next_node_id": "done"},
                    {"label": "Сменный", "next_node_id": "done"},
                ],
            },
            "неизвестно",
            "распознать вариант",
        ),
        (
            {
                "id": "name",
                "type": "question",
                "text": "Имя?",
                "field": "full_name",
                "next_node_id": "done",
            },
            "   ",
            "не должен быть пустым",
        ),
        (
            {
                "id": "age",
                "type": "question",
                "text": "Возраст?",
                "field": "age",
                "validation": "age",
                "next_node_id": "done",
            },
            "двадцать",
            "целым числом",
        ),
        (
            {
                "id": "phone",
                "type": "question",
                "text": "Телефон?",
                "field": "phone",
                "validation": "phone",
                "next_node_id": "done",
            },
            "not-a-phone",
            "Укажите телефон",
        ),
        (
            {
                "id": "email",
                "type": "question",
                "text": "Email?",
                "field": "email",
                "validation": "email",
                "next_node_id": "done",
            },
            "broken@",
            "электронной почты",
        ),
        (
            {
                "id": "unknown",
                "type": "question",
                "text": "Поле?",
                "field": "unsupported",
                "next_node_id": "done",
            },
            "значение",
            "не поддерживается",
        ),
    ],
)
def test_capture_rejects_each_invalid_answer(
    node_payload: dict,
    raw_value: str,
    expected: str,
) -> None:
    """Проверить понятную ошибку для каждого класса некорректного ответа анкеты."""

    engine = AutomationFlowEngine(RecordingCipher())  # type: ignore[arg-type]
    node = (
        AutomationFlowNode.model_construct(**node_payload)
        if node_payload.get("field") == "unsupported"
        else AutomationFlowNode.model_validate(node_payload)
    )
    error, next_node = engine._capture(_candidate(), node, raw_value)

    assert expected in (error or "")
    assert next_node == node.id


def test_capture_encrypts_email_and_updates_vacancy_and_status() -> None:
    """Проверить нормализацию email, синхронизацию вакансии и переход статуса анкеты."""

    cipher = RecordingCipher()
    engine = AutomationFlowEngine(cipher)  # type: ignore[arg-type]
    candidate = _candidate()
    email = AutomationFlowNode.model_validate(
        {
            "id": "email",
            "type": "question",
            "text": "Email?",
            "field": "email",
            "validation": "email",
            "next_node_id": "vacancy",
        }
    )
    vacancy = AutomationFlowNode.model_validate(
        {
            "id": "vacancy",
            "type": "question",
            "text": "Вакансия?",
            "field": "vacancy_key",
            "next_node_id": "done",
        }
    )

    assert engine._capture(candidate, email, " USER@Example.COM ") == (None, "vacancy")
    assert cipher.calls[0][0] == "user@example.com"
    assert engine._field_is_present(candidate, "email") is True
    assert engine._capture(candidate, vacancy, "backend-python") == (None, "done")
    assert candidate.conversation.vacancy_key == "backend-python"
    assert candidate.status == CandidateStatus.QUALIFYING


def test_existing_fields_choose_branch_or_request_choice_again() -> None:
    """Проверить пропуск заполненного вопроса и безопасный повтор неизвестного choice."""

    question = AutomationFlowNode.model_validate(
        {
            "id": "city",
            "type": "question",
            "text": "Город?",
            "field": "city",
            "next_node_id": "done",
        }
    )
    choice = AutomationFlowNode.model_validate(
        {
            "id": "schedule",
            "type": "choice",
            "text": "График?",
            "field": "schedule",
            "options": [
                {"label": "Полный день", "aliases": ["полный"], "next_node_id": "done"},
                {"label": "Сменный", "next_node_id": "done"},
            ],
        }
    )
    engine = AutomationFlowEngine(RecordingCipher())  # type: ignore[arg-type]

    assert engine._next_after_existing(question, _candidate(city="Москва")) == "done"
    assert engine._next_after_existing(choice, _candidate(schedule=" полный ")) == "done"
    assert engine._next_after_existing(choice, _candidate(schedule="гибрид")) is None


def test_process_skips_existing_question_and_completes_handoff() -> None:
    """Проверить автоматический пропуск заполненного поля и handoff-терминал графа."""

    definition = {
        "version": 1,
        "start_node_id": "city",
        "nodes": [
            {
                "id": "city",
                "type": "question",
                "text": "Город?",
                "field": "city",
                "skip_if_present": True,
                "next_node_id": "handoff",
            },
            {"id": "handoff", "type": "handoff", "text": "Передаю оператору"},
        ],
    }
    conversation = _conversation(_candidate(city="Москва"))
    result = AutomationFlowEngine(RecordingCipher()).process(  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        conversation=conversation,
        flow=_flow(definition),
        message_text="ignored",
    )

    assert result.completed and result.handoff
    assert result.reply_text == "Передаю оператору"
    assert conversation.flow_state_json["completion_mode"] == "handoff"


def test_process_fails_closed_for_missing_node_and_iteration_limit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить handoff при повреждённой ссылке и при превышении защитного лимита графа."""

    engine = AutomationFlowEngine(RecordingCipher())  # type: ignore[arg-type]
    missing_definition = SimpleNamespace(start_node_id="missing", nodes=[])
    monkeypatch.setattr(
        "app.services.flows.AutomationFlowDefinition.model_validate",
        lambda _value: missing_definition,
    )
    missing_conversation = _conversation()
    missing = engine.process(
        SimpleNamespace(),  # type: ignore[arg-type]
        conversation=missing_conversation,
        flow=_flow({}),
        message_text="ignored",
    )
    assert missing.completed and missing.handoff
    assert "ошибки конфигурации" in (missing.reply_text or "")

    looping_node = SimpleNamespace(id="loop", type="message", text="", next_node_id="loop")
    looping_definition = SimpleNamespace(start_node_id="loop", nodes=[looping_node])
    monkeypatch.setattr(
        "app.services.flows.AutomationFlowDefinition.model_validate",
        lambda _value: looping_definition,
    )
    looping_conversation = _conversation()
    looping = engine.process(
        SimpleNamespace(),  # type: ignore[arg-type]
        conversation=looping_conversation,
        flow=_flow({}),
        message_text="ignored",
    )
    assert looping.completed and looping.handoff
    assert len(looping_conversation.flow_state_json["visited_nodes"]) == 60
