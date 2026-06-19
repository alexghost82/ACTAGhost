"""Security Agent — enforce permissions, encryption and audit."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult


class SecurityAgent(BaseAgent):
    NAME = "security"
    SUB_PROMPT = (
        "Обеспечь безопасность данных и действий, веди аудит и контролируй "
        "доступ агентов."
    )

    def handle(self, state, result: AgentResult) -> None:
        self.s.permissions.require(self.NAME, "permission.check")
        checks: dict[str, bool] = {}

        # Verify each assigned worker has the capabilities its task implies.
        for task_id, agent in state.strategy.assignments.items():
            needs = "integration.network" if agent == "automation" else "reason"
            checks[f"{agent}:{needs}"] = self.s.permissions.has(agent, needs)

        # Confirm data-at-rest encryption is active (round-trip a probe).
        probe = "acta-security-probe"
        try:
            enc = self.s.memory.crypto.encrypt(probe)
            encryption_ok = self.s.memory.crypto.decrypt(enc) == probe
        except Exception:
            encryption_ok = False

        flagged = [k for k, ok in checks.items() if not ok]
        self.s.audit.record(
            self.NAME,
            "review",
            encryption_ok=encryption_ok,
            permission_flags=flagged,
            request_id=state.request.request_id,
        )

        state.artifacts["security"] = {
            "encryption_ok": encryption_ok,
            "permission_checks": checks,
            "flagged": flagged,
        }
        result.output = state.artifacts["security"]
        result.ok = encryption_ok
        result.summary = f"encryption={'ok' if encryption_ok else 'FAIL'} flags={len(flagged)}"
