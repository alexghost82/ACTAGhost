"""Shared data contracts for ACTA.

These pydantic models are the typed messages that flow between the cognitive
core, the agents and the orchestrator. Keeping them in one place makes the
agent pipeline self-documenting and easy to test.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class IntentType(str, Enum):
    QUESTION = "question"
    TASK = "task"
    COMMAND = "command"
    AUTOMATION = "automation"
    RESEARCH = "research"
    SMALL_TALK = "small_talk"
    UNKNOWN = "unknown"


class Modality(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    VIDEO = "video"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class MemoryKind(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PERSONAL = "personal"
    PROCEDURAL = "procedural"


# --------------------------------------------------------------------------- #
# Request / Intent / Context
# --------------------------------------------------------------------------- #
class UserRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: _uid("req"))
    user_id: str = "default"
    text: str = ""
    modality: Modality = Modality.TEXT
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)


class Intent(BaseModel):
    type: IntentType = IntentType.UNKNOWN
    summary: str = ""
    objectives: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    requires_external: bool = False
    confidence: float = 0.0


class UserContext(BaseModel):
    user_id: str = "default"
    goals: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
    preferences: dict[str, Any] = Field(default_factory=dict)
    recent_events: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=_now)


# --------------------------------------------------------------------------- #
# Reasoning / Planning / Decision
# --------------------------------------------------------------------------- #
class ReasoningChain(BaseModel):
    goal: str = ""
    steps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    conclusion: str = ""


class PlanTask(BaseModel):
    id: str = Field(default_factory=lambda: _uid("task"))
    title: str = ""
    description: str = ""
    agent: str = "research"
    depends_on: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: dict[str, Any] | None = None


class Plan(BaseModel):
    goal: str = ""
    tasks: list[PlanTask] = Field(default_factory=list)


class Strategy(BaseModel):
    name: str = "sequential"
    rationale: str = ""
    # Mapping of task id -> chosen worker agent name
    assignments: dict[str, str] = Field(default_factory=dict)
    # Mapping of task id -> chosen model provider name
    model_routing: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Agent execution results
# --------------------------------------------------------------------------- #
class AgentResult(BaseModel):
    agent: str
    ok: bool = True
    output: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error: str | None = None
    started_at: float = Field(default_factory=_now)
    finished_at: float | None = None

    def done(self) -> AgentResult:
        self.finished_at = _now()
        return self

    @property
    def duration_ms(self) -> float:
        if self.finished_at is None:
            return 0.0
        return round((self.finished_at - self.started_at) * 1000, 2)


# --------------------------------------------------------------------------- #
# Final assistant response
# --------------------------------------------------------------------------- #
class TraceEntry(BaseModel):
    step: int
    agent: str
    summary: str
    ok: bool = True
    duration_ms: float = 0.0


class ActaResponse(BaseModel):
    request_id: str
    answer: str = ""
    language: str = "ru"
    intent: Intent | None = None
    plan: Plan | None = None
    strategy: Strategy | None = None
    trace: list[TraceEntry] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)
