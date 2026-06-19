"""Integration Agent — talk to external services and local resources."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult


class IntegrationAgent(BaseAgent):
    NAME = "integration"
    SUB_PROMPT = (
        "Выполни интеграцию с указанным внешним сервисом или устройством через "
        "соответствующий API или протокол."
    )

    def handle(self, state, result: AgentResult) -> None:
        # Only act when the request actually needs an external integration.
        if not state.intent.requires_external:
            result.summary = "no external integration required"
            result.output = {"skipped": True}
            return

        self.s.permissions.require(self.NAME, "integration.network")
        # Explicit integration directives can be passed via request metadata.
        directive = state.request.metadata.get("integration")
        if directive:
            out = self.s.connectors.execute(
                directive.get("connector", "echo"),
                directive.get("action", "run"),
                directive.get("params", {}),
            )
        else:
            # Safe default: record the integration intent without side effects.
            out = self.s.connectors.execute(
                "echo",
                "plan_integration",
                {"objectives": state.intent.objectives, "entities": state.intent.entities},
            )
        state.artifacts["integration"] = out
        result.output = out
        result.ok = bool(out.get("ok", True))
        result.summary = f"connector={out.get('connector', directive and directive.get('connector'))}"
