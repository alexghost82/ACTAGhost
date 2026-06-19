"""Base agent and the shared service bundle injected into every agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from acta.config import Settings, get_settings
from acta.integration import ConnectorRegistry, default_registry
from acta.knowledge_graph import KnowledgeGraph
from acta.logging_config import get_logger
from acta.memory import MemoryStore
from acta.multimodal import MultimodalProcessor
from acta.providers import AIRouter, ChatMessage
from acta.schemas import AgentResult
from acta.security import AuditLog, PermissionRegistry

if TYPE_CHECKING:
    from acta.orchestrator.state import PipelineState


@dataclass
class AgentServices:
    """Everything an agent might need, constructed once and shared."""

    settings: Settings
    router: AIRouter
    memory: MemoryStore
    kg: KnowledgeGraph
    audit: AuditLog
    permissions: PermissionRegistry
    connectors: ConnectorRegistry
    multimodal: MultimodalProcessor

    @classmethod
    def build(cls, settings: Settings | None = None) -> "AgentServices":
        settings = settings or get_settings()
        memory = MemoryStore(settings)
        permissions = PermissionRegistry()
        if not settings.allow_system_control:
            for agent in ("system", "integration"):
                permissions.revoke(agent, "system.control")
        return cls(
            settings=settings,
            router=AIRouter(settings),
            memory=memory,
            kg=KnowledgeGraph(settings),
            audit=AuditLog(settings),
            permissions=permissions,
            connectors=default_registry(settings),
            multimodal=MultimodalProcessor(),
        )


class BaseAgent:
    """Common behavior for all agents.

    Subclasses set ``NAME`` and ``SUB_PROMPT`` and implement :meth:`handle`.
    The public :meth:`run` wraps execution with audit logging and error capture
    so the orchestrator always receives a well-formed :class:`AgentResult`.
    """

    NAME: str = "base"
    SUB_PROMPT: str = ""
    ROUTING_PROFILE: str = "default"

    def __init__(self, services: AgentServices) -> None:
        self.s = services
        self.log = get_logger(f"agent.{self.NAME}")

    # -- public entrypoint ------------------------------------------------- #
    def run(self, state: "PipelineState") -> AgentResult:
        result = AgentResult(agent=self.NAME)
        self.s.audit.record(self.NAME, "start", request_id=state.request.request_id)
        try:
            self.handle(state, result)
        except Exception as exc:  # capture so the pipeline can continue
            result.ok = False
            result.error = f"{type(exc).__name__}: {exc}"
            self.log.exception("agent %s failed", self.NAME)
        result.done()
        self.s.audit.record(
            self.NAME, "finish", ok=result.ok, ms=result.duration_ms, summary=result.summary
        )
        state.results[self.NAME] = result
        return result

    def handle(self, state: "PipelineState", result: AgentResult) -> None:
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------- #
    def llm(self, user: str, *, system_extra: str = "", mock_kind: str = "answer",
            profile: str | None = None, temperature: float = 0.2, lang: str | None = None) -> str:
        """Call the routed model with this agent's sub-prompt as system role."""
        system = self.SUB_PROMPT
        if system_extra:
            system = f"{system}\n\n{system_extra}"
        if lang:
            from acta.i18n import respond_in_directive

            system = f"{system}\n{respond_in_directive(lang)}"
        # Directive understood by the offline mock provider; ignored by real models.
        system = f"{system}\n[[ACTA_MOCK:{mock_kind}]]"
        messages = [ChatMessage("system", system), ChatMessage("user", user)]
        resp = self.s.router.complete(
            messages, profile=profile or self.ROUTING_PROFILE, temperature=temperature
        )
        return resp.text
