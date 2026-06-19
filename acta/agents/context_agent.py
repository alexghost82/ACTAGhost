"""Context Agent — build and update the dynamic user model."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult, IntentType, MemoryKind, UserContext


class ContextAgent(BaseAgent):
    NAME = "context"
    SUB_PROMPT = (
        "Обнови контекст пользователя, включая цели, проекты, привычки и историю, "
        "на основе новых данных и событий."
    )

    def handle(self, state, result: AgentResult) -> None:
        self.s.permissions.require(self.NAME, "memory.read")
        user_id = state.request.user_id
        mem = self.s.memory

        # Load the persisted personal profile.
        profile = mem.get_personal("profile", {}, user_id=user_id) or {}
        ctx = UserContext(
            user_id=user_id,
            goals=profile.get("goals", []),
            projects=profile.get("projects", []),
            habits=profile.get("habits", []),
            preferences=profile.get("preferences", {}),
        )

        # Recent episodic events feed short-term context.
        recent = mem.recent(MemoryKind.EPISODIC, user_id=user_id, limit=5)
        ctx.recent_events = [r.content for r in recent]

        # Relevant semantic facts for this request.
        text = state.normalized.get("text") or state.request.text
        facts = mem.search(text, kind=MemoryKind.SEMANTIC, user_id=user_id, limit=5)
        ctx.facts = [f.content for f in facts]

        # Derive new goals/projects from the current intent and persist them.
        if state.intent.type in (IntentType.TASK, IntentType.RESEARCH, IntentType.AUTOMATION):
            for obj in state.intent.objectives[:2]:
                if obj and obj not in ctx.goals:
                    ctx.goals.append(obj)
        self.s.permissions.require(self.NAME, "memory.write")
        mem.set_personal(
            "profile",
            {
                "goals": ctx.goals[-20:],
                "projects": ctx.projects[-20:],
                "habits": ctx.habits[-20:],
                "preferences": ctx.preferences,
            },
            user_id=user_id,
        )

        state.context = ctx
        result.output = ctx.model_dump()
        result.summary = (
            f"goals={len(ctx.goals)} facts={len(ctx.facts)} events={len(ctx.recent_events)}"
        )
