"""UI Agent — present results to the user in a clear, consolidated form."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.i18n import t
from acta.schemas import AgentResult


class UIAgent(BaseAgent):
    NAME = "ui"
    SUB_PROMPT = (
        "Обеспечь корректное отображение данных и взаимодействие пользователя с "
        "системой."
    )

    def handle(self, state, result: AgentResult) -> None:
        self.s.permissions.require(self.NAME, "respond")
        lang = getattr(state, "language", "ru")

        # Gather the worker outputs produced during execution.
        task_outputs = []
        for task in state.plan.tasks:
            if task.result:
                task_outputs.append(task.result.get("text") or task.result.get("summary") or "")
        body = "\n\n".join(o for o in task_outputs if o).strip()

        if self.s.router.real_available():
            # A real model is available: synthesize a polished, self-contained
            # answer in the user's language, integrating all worker results.
            answer = self._synthesize(state, body, lang) or body
        else:
            # Offline (mock): deterministic composition with the reasoning trace.
            if not body:
                body = self.llm(
                    f"Дай пользователю полезный ответ.\nЗапрос: {state.request.text}",
                    mock_kind="answer",
                    lang=lang,
                ).strip()
            answer = body
            if state.intent.type.value in ("task", "research", "automation", "command"):
                steps = "\n".join(
                    f"  {i}. {s}" for i, s in enumerate(state.reasoning.steps[:5], 1)
                )
                if steps:
                    answer = f"{body}\n\n{t('how_i_got_here', lang)}\n{steps}"

        state.answer = answer or t("empty_answer", lang)
        result.output = {"answer": state.answer, "synthesized": self.s.router.real_available()}
        result.summary = f"answer_len={len(state.answer)} lang={lang}"

    def _synthesize(self, state, worker_results: str, lang: str) -> str:
        """Produce the final answer with the routed model, in the user's language."""
        facts = "; ".join(state.context.facts[:4])
        prompt = (
            f"Запрос пользователя: {state.request.text}\n"
            f"Намерение: {state.intent.type.value} — {state.intent.summary}\n"
            f"Вывод рассуждения: {state.reasoning.conclusion}\n"
            f"Известные факты: {facts or 'нет'}\n"
            f"Результаты работы агентов:\n{worker_results or '(нет результатов)'}\n\n"
            "Составь полный, понятный и полезный итоговый ответ пользователю. "
            "Интегрируй результаты агентов, не упоминай внутреннюю кухню."
        )
        return self.llm(prompt, mock_kind="answer", lang=lang, temperature=0.3).strip()
