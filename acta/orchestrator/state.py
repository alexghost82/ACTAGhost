"""Mutable state object threaded through the agent pipeline.

This mirrors the shared-state model used by LangGraph: each node (agent) reads
from and writes to a single state object, which makes the data flow explicit and
easy to inspect or persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acta.schemas import (
    ActaResponse,
    AgentResult,
    Intent,
    Plan,
    ReasoningChain,
    Strategy,
    TraceEntry,
    UserContext,
    UserRequest,
)


@dataclass
class PipelineState:
    request: UserRequest
    language: str = "ru"
    normalized: dict[str, Any] = field(default_factory=dict)
    intent: Intent = field(default_factory=Intent)
    context: UserContext = field(default_factory=UserContext)
    reasoning: ReasoningChain = field(default_factory=ReasoningChain)
    plan: Plan = field(default_factory=Plan)
    strategy: Strategy = field(default_factory=Strategy)
    retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
    kg_findings: list[dict[str, Any]] = field(default_factory=list)
    results: dict[str, AgentResult] = field(default_factory=dict)
    trace: list[TraceEntry] = field(default_factory=list)
    answer: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)

    def add_trace(self, step: int, result: AgentResult) -> None:
        self.trace.append(
            TraceEntry(
                step=step,
                agent=result.agent,
                summary=result.summary or (result.error or ""),
                ok=result.ok,
                duration_ms=result.duration_ms,
            )
        )

    def to_response(self) -> ActaResponse:
        return ActaResponse(
            request_id=self.request.request_id,
            answer=self.answer,
            language=self.language,
            intent=self.intent,
            plan=self.plan,
            strategy=self.strategy,
            trace=self.trace,
            artifacts=self.artifacts,
        )
