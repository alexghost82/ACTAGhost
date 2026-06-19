"""Memory Agent — retrieve relevant memories and persist new ones."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult, MemoryKind


class MemoryAgent(BaseAgent):
    NAME = "memory"
    SUB_PROMPT = (
        "Обнови и извлеки релевантные данные из памяти для поддержки текущих "
        "задач и контекста."
    )

    def retrieve(self, state, result: AgentResult) -> None:
        """Pre-execution: pull relevant memories into the pipeline state."""
        self.s.permissions.require(self.NAME, "memory.read")
        text = state.normalized.get("text") or state.request.text
        user_id = state.request.user_id
        hits = self.s.memory.search(text, user_id=user_id, limit=6)
        state.retrieved_memories = [h.to_dict() for h in hits]
        result.output = {"retrieved": state.retrieved_memories}
        result.summary = f"retrieved={len(hits)}"

    def persist(self, state, result: AgentResult) -> None:
        """Post-execution: write an episodic record of this interaction."""
        self.s.permissions.require(self.NAME, "memory.write")
        user_id = state.request.user_id
        text = state.normalized.get("text") or state.request.text
        self.s.memory.add(
            MemoryKind.EPISODIC,
            f"User: {text}\nACTA: {state.answer[:500]}",
            tags=[state.intent.type.value],
            metadata={"request_id": state.request.request_id},
            user_id=user_id,
        )
        # Store derived facts as semantic memory.
        for fact in state.intent.objectives[:3]:
            if fact and len(fact) > 8:
                self.s.memory.add(
                    MemoryKind.SEMANTIC, fact, tags=["objective"], user_id=user_id
                )
        result.output = {"stored": True, "stats": self.s.memory.stats(user_id=user_id)}
        result.summary = "memory updated"

    # default handle = retrieval (used when invoked generically)
    def handle(self, state, result: AgentResult) -> None:
        self.retrieve(state, result)
