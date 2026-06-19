"""Reasoning Agent — build a logical chain toward the goal."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult, ReasoningChain


class ReasoningAgent(BaseAgent):
    NAME = "reasoning"
    ROUTING_PROFILE = "reasoning"
    SUB_PROMPT = (
        "Построй логическую цепочку рассуждений для достижения цели, учитывая "
        "контекст и доступные знания."
    )

    def handle(self, state, result: AgentResult) -> None:
        goal = state.intent.summary or state.request.text
        facts = state.context.facts
        kg_hits = [h.get("id") for h in state.kg_findings]

        steps = [
            "Уточнить цель и ожидаемый результат.",
            "Собрать релевантный контекст из памяти и графа знаний.",
        ]
        if facts:
            steps.append(f"Учесть известные факты: {', '.join(facts[:3])}.")
        if kg_hits:
            steps.append(f"Использовать связанные сущности: {', '.join(map(str, kg_hits[:3]))}.")
        if state.intent.requires_external:
            steps.append("Определить требуемые внешние сервисы/инструменты.")
        steps.append("Декомпозировать задачу на исполнимые подзадачи.")
        steps.append("Проверить ограничения и риски, выбрать безопасный путь.")

        chain = ReasoningChain(
            goal=goal,
            steps=steps,
            assumptions=self._assumptions(state),
            conclusion="",
        )
        chain.conclusion = self.llm(
            "Сформулируй краткий вывод (1-2 предложения) о том, как достичь цели.\n"
            f"Цель: {goal}\nШаги: {'; '.join(steps)}",
            mock_kind="reasoning",
            lang=getattr(state, "language", None),
        ).strip()

        state.reasoning = chain
        result.output = chain.model_dump()
        result.summary = f"steps={len(chain.steps)}"

    def _assumptions(self, state) -> list[str]:
        out = []
        if not state.context.facts:
            out.append("Дополнительные факты не найдены в памяти; используем общие знания.")
        if state.intent.confidence < 0.6:
            out.append("Намерение определено с умеренной уверенностью.")
        return out
