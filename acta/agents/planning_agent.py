"""Planning Agent — decompose the goal into an executable plan."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult, IntentType, Plan, PlanTask


_SYSTEM_CUES = (
    "open ", "launch", "run program", "start program", "kill", "terminate", "process",
    "service", "shutdown", "reboot", "delete file", "create file", "system info",
    "запусти", "открой", "закрой", "останови", "процесс", "сервис", "служб",
    "создай файл", "удали файл", "перезагруз", "выполни команду", "о системе",
    "הרץ", "פתח", "סגור", "עצור", "תהליך", "שירות", "צור קובץ", "מחק קובץ", "מידע על המערכת",
)


def _pick_worker(text: str, requires_external: bool) -> str:
    low = text.lower()
    if any(k in low for k in _SYSTEM_CUES):
        return "system"
    if any(k in low for k in ("code", "implement", "script", "function", "код", "напиши код")):
        return "coding"
    if requires_external or any(k in low for k in ("api", "http", "send", "fetch", "отправ", "запрос")):
        return "automation"
    return "research"


class PlanningAgent(BaseAgent):
    NAME = "planning"
    ROUTING_PROFILE = "planning"
    SUB_PROMPT = (
        "Разработай подробный план выполнения задачи с разбивкой на подзадачи и "
        "назначением агентов."
    )

    def handle(self, state, result: AgentResult) -> None:
        goal = state.intent.summary or state.request.text
        objectives = state.intent.objectives or [goal]
        plan = Plan(goal=goal)

        # Small talk / pure questions need no multi-step plan.
        if state.intent.type in (IntentType.SMALL_TALK, IntentType.QUESTION) and len(objectives) <= 1:
            plan.tasks.append(
                PlanTask(
                    title="Ответить пользователю",
                    description=goal,
                    agent="research",
                )
            )
        else:
            prev_id: str | None = None
            for i, obj in enumerate(objectives, start=1):
                worker = _pick_worker(obj, state.intent.requires_external)
                task = PlanTask(
                    title=f"Шаг {i}",
                    description=obj,
                    agent=worker,
                    depends_on=[prev_id] if prev_id else [],
                )
                plan.tasks.append(task)
                prev_id = task.id
            # Always finish with a synthesis/research step if multiple tasks.
            if len(plan.tasks) > 1:
                synth = PlanTask(
                    title="Интеграция результатов",
                    description="Свести результаты подзадач в единый ответ.",
                    agent="research",
                    depends_on=[t.id for t in plan.tasks],
                )
                plan.tasks.append(synth)

        state.plan = plan
        result.output = plan.model_dump()
        result.summary = f"tasks={len(plan.tasks)}"
