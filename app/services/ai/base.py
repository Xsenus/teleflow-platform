from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class AIResult:
    reply_text: str
    candidate_updates: dict[str, Any] = field(default_factory=dict)
    vacancy_key: str | None = None
    handoff: bool = False
    safety_flags: dict[str, Any] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None


class AIProvider(Protocol):
    """Контракт AI-провайдера, используемого сервисом автоматизации."""

    @property
    def name(self) -> str:
        """Вернуть стабильный идентификатор типа провайдера."""
        ...

    @property
    def model_name(self) -> str | None:
        """Вернуть имя модели либо ``None`` для провайдера без модели."""
        ...

    def generate(
        self,
        *,
        message: str,
        history: list[dict[str, str]],
        knowledge: list[dict[str, str]],
        candidate: dict[str, Any],
        system_prompt: str,
    ) -> AIResult:
        """Выполнить операцию generate класса AIProvider. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        ...
