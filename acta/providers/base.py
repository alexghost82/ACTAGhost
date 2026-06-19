"""Provider abstraction shared by all model backends."""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    raw: dict[str, Any] = field(default_factory=dict)

    def as_json(self, default: Any = None) -> Any:
        """Best-effort parse of the response text as JSON.

        Tolerates fenced code blocks and leading/trailing prose so callers can
        ask a model for structured output without brittle parsing.
        """
        text = self.text.strip()
        if "```" in text:
            # Pull the content of the first fenced block.
            parts = text.split("```")
            for chunk in parts:
                chunk = chunk.strip()
                if chunk.startswith("json"):
                    chunk = chunk[len("json"):].strip()
                if chunk.startswith("{") or chunk.startswith("["):
                    text = chunk
                    break
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # Try to locate the outermost JSON object/array.
            for opener, closer in (("{", "}"), ("[", "]")):
                start = text.find(opener)
                end = text.rfind(closer)
                if start != -1 and end != -1 and end > start:
                    try:
                        return json.loads(text[start : end + 1])
                    except (json.JSONDecodeError, ValueError):
                        continue
        return default


class LLMProvider(abc.ABC):
    """Base class for every model backend."""

    name: str = "base"

    def __init__(self, model: str) -> None:
        self.model = model

    @abc.abstractmethod
    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        ...

    def is_available(self) -> bool:
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} model={self.model!r}>"
