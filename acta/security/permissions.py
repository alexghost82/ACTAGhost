"""Capability-based permissions for agents.

Each agent is granted a set of capabilities (e.g. ``memory.read``,
``integration.network``). The Security Agent checks capabilities before
sensitive operations and the registry refuses anything not explicitly granted.
"""

from __future__ import annotations


class PermissionDenied(Exception):
    pass


# Default least-privilege grants per agent role.
DEFAULT_GRANTS: dict[str, set[str]] = {
    "ui": {"respond"},
    "intent": {"reason"},
    "context": {"memory.read", "memory.write", "kg.read"},
    "reasoning": {"reason", "kg.read", "memory.read"},
    "planning": {"reason"},
    "decision": {"reason", "route"},
    "orchestrator": {"orchestrate", "agent.invoke"},
    "memory": {"memory.read", "memory.write"},
    "knowledge_graph": {"kg.read", "kg.write"},
    "integration": {"integration.network", "integration.fs", "system.control"},
    "security": {"audit", "permission.check"},
    "multimodal": {"media.process"},
    # specialized worker agents
    "research": {"reason", "kg.read", "memory.read"},
    "automation": {"integration.network", "integration.fs"},
    "coding": {"reason"},
    # system worker has full OS control capabilities
    "system": {
        "system.control",
        "system.exec",
        "system.process",
        "system.service",
        "system.fs",
        "integration.network",
        "integration.fs",
    },
}


class PermissionRegistry:
    def __init__(self, grants: dict[str, set[str]] | None = None) -> None:
        self._grants = {k: set(v) for k, v in (grants or DEFAULT_GRANTS).items()}

    def grant(self, agent: str, capability: str) -> None:
        self._grants.setdefault(agent, set()).add(capability)

    def revoke(self, agent: str, capability: str) -> None:
        self._grants.setdefault(agent, set()).discard(capability)

    def has(self, agent: str, capability: str) -> bool:
        return capability in self._grants.get(agent, set())

    def require(self, agent: str, capability: str, *, principal_role: str | None = None) -> None:
        if not self.has(agent, capability):
            raise PermissionDenied(
                f"agent '{agent}' lacks capability '{capability}'"
            )
        # SEC-8: role-aware guard for system control, not just static capabilities.
        if capability == "system.control" and (principal_role or "admin").lower() != "admin":
            raise PermissionDenied("system control is restricted to admin principals")

    def capabilities(self, agent: str) -> set[str]:
        return set(self._grants.get(agent, set()))
