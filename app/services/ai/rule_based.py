from __future__ import annotations

import re
from typing import Any

from app.services.ai.base import AIResult
from app.services.redaction import detect_prompt_injection

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_AGE = re.compile(r"(?<!\d)(1[4-9]|[2-9]\d|1[01]\d|120)(?:\s*(?:лет|года?|год))?(?!\d)", re.I)
_CITY = re.compile(
    r"(?:живу\s+в|город\s*[:—-]?|из\s+города?)\s+([А-ЯЁA-Z][А-Яа-яЁёA-Za-z -]{1,60})", re.I
)
_NAME = re.compile(r"(?:меня\s+зовут|имя\s*[:—-]?)\s+([А-ЯЁA-Z][А-Яа-яЁёA-Za-z -]{1,80})", re.I)
_EXPERIENCE = re.compile(r"(?:опыт(?:\s+работы)?\s*[:—-]?|работал[аи]?\s+)(.{3,500})", re.I)
_SCHEDULE = re.compile(r"(?:график\s*[:—-]?|могу\s+работать\s+)(.{2,160})", re.I)


class RuleBasedProvider:
    name = "rule_based"
    model_name = "deterministic-v1"

    def generate(
        self,
        *,
        message: str,
        history: list[dict[str, str]],
        knowledge: list[dict[str, str]],
        candidate: dict[str, Any],
        system_prompt: str,
    ) -> AIResult:
        """Выполнить операцию generate класса RuleBasedProvider. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        del history, system_prompt
        flags: dict[str, Any] = {}
        patterns = detect_prompt_injection(message)
        if patterns:
            flags["prompt_injection_detected"] = True
            flags["matched_patterns"] = patterns

        updates: dict[str, Any] = {}
        match = _EMAIL.search(message)
        if match:
            updates["email"] = match.group(0)
        match = _PHONE.search(message)
        if match:
            updates["phone"] = match.group(0).strip()
        match = _AGE.search(message)
        if match:
            updates["age"] = int(match.group(1))
        match = _CITY.search(message)
        if match:
            updates["city"] = match.group(1).strip(" .,!?:;-")
        match = _NAME.search(message)
        if match:
            updates["full_name"] = match.group(1).strip(" .,!?:;-")
        match = _EXPERIENCE.search(message)
        if match:
            updates["experience"] = match.group(1).strip()[:1000]
        match = _SCHEDULE.search(message)
        if match:
            updates["schedule"] = match.group(1).strip()[:250]

        merged = {**candidate, **updates}
        missing: list[tuple[str, str]] = [
            ("full_name", "Как к вам обращаться?"),
            ("city", "В каком городе вы находитесь?"),
            ("age", "Подскажите ваш возраст."),
            ("experience", "Расскажите, пожалуйста, о релевантном опыте работы."),
            ("schedule", "Какой график работы вам подходит?"),
        ]
        reply = ""
        for field, question in missing:
            if not merged.get(field):
                reply = question
                break

        if not reply:
            reply = (
                "Спасибо, основные данные собраны. Передаю вашу анкету менеджеру, "
                "он свяжется с вами для следующего шага."
            )
            updates["status"] = "ready_for_review"
            updates["summary"] = self._summary(merged)

        lowered = message.lower()
        for article in knowledge:
            tags = [str(tag).lower() for tag in article.get("tags", [])]
            if any(tag and tag in lowered for tag in tags):
                reply = f"{article['content'][:1200]}\n\n{reply}".strip()
                break

        if flags.get("prompt_injection_detected"):
            reply = "Я могу помочь только по вопросам вакансии и анкеты кандидата. " + reply

        return AIResult(
            reply_text=reply[:4000],
            candidate_updates=updates,
            safety_flags=flags,
        )

    @staticmethod
    def _summary(candidate: dict[str, Any]) -> str:
        """Реализовать внутренний этап summary step класса RuleBasedProvider. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        parts = []
        labels = {
            "full_name": "Имя",
            "city": "Город",
            "age": "Возраст",
            "experience": "Опыт",
            "schedule": "График",
        }
        for key, label in labels.items():
            if candidate.get(key):
                parts.append(f"{label}: {candidate[key]}")
        return "; ".join(parts)[:8000]
