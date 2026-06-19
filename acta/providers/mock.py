"""Deterministic offline provider.

The mock provider lets ACTA run end-to-end with zero credentials and makes the
test-suite fully deterministic. It produces structured, context-aware text by
reading an optional ``[[ACTA_MOCK:<kind>]]`` directive placed in the system
message by an agent, and otherwise returns a sensible synthesized reply.
"""

from __future__ import annotations

import json
import re

from acta.providers.base import ChatMessage, LLMProvider, LLMResponse

_DIRECTIVE = re.compile(r"\[\[ACTA_MOCK:(?P<kind>[a-z_]+)\]\]")


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, model: str = "acta-mock-1") -> None:
        super().__init__(model)

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        system = "\n".join(m.content for m in messages if m.role == "system")
        user = "\n".join(m.content for m in messages if m.role == "user").strip()
        kind = "answer"
        match = _DIRECTIVE.search(system)
        if match:
            kind = match.group("kind")

        text = self._render(kind, user, system)
        return LLMResponse(text=text, provider=self.name, model=self.model)

    # -- rendering helpers ------------------------------------------------- #
    def _render(self, kind: str, user: str, system: str) -> str:
        handler = getattr(self, f"_kind_{kind}", None)
        if handler is None:
            return self._kind_answer(user, system)
        return handler(user, system)

    def _kind_answer(self, user: str, system: str) -> str:
        snippet = user.strip().split("\n")[0][:280] if user else "your request"
        return (
            f"Here is ACTA's synthesized response regarding: {snippet}\n\n"
            "I analysed the intent, refreshed your context, reasoned through the "
            "objective, planned the work, routed it to the appropriate agents and "
            "integrated their results. See the execution trace for the full "
            "step-by-step breakdown."
        )

    def _kind_reasoning(self, user: str, system: str) -> str:
        return (
            "Reasoning narrative: I clarified the goal, identified the relevant "
            "facts from memory and the knowledge graph, considered the available "
            "agents, and selected the lowest-risk path that satisfies the stated "
            "constraints."
        )

    def _kind_summary(self, user: str, system: str) -> str:
        first = user.strip().split("\n")[0][:160] if user else ""
        return f"Summary: {first}" if first else "Summary: task completed."

    def _kind_json(self, user: str, system: str) -> str:
        # Generic structured echo used when an agent explicitly wants JSON.
        return json.dumps({"echo": user[:400], "ok": True}, ensure_ascii=False)

    def _kind_system(self, user: str, system: str) -> str:
        # Offline-safe: default to a read-only system info action.
        return json.dumps({"action": "info", "params": {}}, ensure_ascii=False)
