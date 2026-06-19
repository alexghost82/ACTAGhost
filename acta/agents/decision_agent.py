"""Decision Agent — choose a strategy and route tasks to agents/models."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult, Strategy

# Map worker agent -> preferred model routing profile (consumed by AIRouter).
_WORKER_PROFILE = {
    "research": "reasoning",
    "coding": "coding",
    "automation": "fast",
    "system": "fast",
}


class DecisionAgent(BaseAgent):
    NAME = "decision"
    SUB_PROMPT = (
        "Определи оптимальную стратегию выполнения задачи и назначь "
        "специализированных агентов с учётом доступных ресурсов."
    )

    def handle(self, state, result: AgentResult) -> None:
        self.s.permissions.require(self.NAME, "route")
        tasks = state.plan.tasks
        has_deps = any(t.depends_on for t in tasks)
        # Tasks with no interdependencies can run in parallel.
        parallelizable = len(tasks) > 1 and not has_deps
        name = "parallel" if parallelizable else "sequential"

        assignments = {t.id: t.agent for t in tasks}
        available = set(self.s.router.available_providers())
        model_routing = {}
        for t in tasks:
            profile = _WORKER_PROFILE.get(t.agent, "default")
            provider = self.s.router.select(profile).name
            model_routing[t.id] = provider if provider in available or provider == "mock" else "mock"

        strategy = Strategy(
            name=name,
            rationale=(
                f"{len(tasks)} подзадач(и); "
                f"{'независимы — выполняем параллельно' if parallelizable else 'есть зависимости — последовательно'}."
            ),
            assignments=assignments,
            model_routing=model_routing,
        )
        state.strategy = strategy
        result.output = strategy.model_dump()
        result.summary = f"strategy={name} agents={len(set(assignments.values()))}"
