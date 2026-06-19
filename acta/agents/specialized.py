"""Specialized worker agents invoked by the orchestrator to execute tasks.

These are the "hands" of ACTA: each consumes a single :class:`PlanTask`,
performs the work for its domain and returns a structured result that the UI
Agent later integrates into the final answer.
"""

from __future__ import annotations

import json
import platform
import re
from typing import TYPE_CHECKING

from acta.agents.base import BaseAgent
from acta.i18n import t
from acta.providers.base import ChatMessage
from acta.schemas import AgentResult, PlanTask

if TYPE_CHECKING:
    from acta.orchestrator.state import PipelineState


class WorkerAgent(BaseAgent):
    """Base for agents that execute an individual plan task."""

    def execute_task(self, state: "PipelineState", task: PlanTask) -> dict:
        raise NotImplementedError

    def handle(self, state, result: AgentResult) -> None:
        # Workers are normally invoked per-task via :meth:`execute_task`; this
        # generic handle executes every task assigned to this worker.
        outputs = []
        for task in state.plan.tasks:
            if task.agent == self.NAME:
                outputs.append(self.execute_task(state, task))
        result.output = {"tasks": outputs}
        result.summary = f"executed={len(outputs)}"


class ResearchAgent(WorkerAgent):
    NAME = "research"
    ROUTING_PROFILE = "reasoning"
    SUB_PROMPT = (
        "Ты исследовательский агент ACTA. Проанализируй подзадачу, используй "
        "память и граф знаний, дай обоснованный и краткий результат."
    )

    def execute_task(self, state, task: PlanTask) -> dict:
        context = "; ".join(state.context.facts[:3] + [h.get("id", "") for h in state.kg_findings[:3]])
        text = self.llm(
            f"Подзадача: {task.description}\nКонтекст: {context or 'нет'}\n"
            "Дай результат по подзадаче.",
            mock_kind="summary",
            lang=getattr(state, "language", None),
        ).strip()
        return {"task_id": task.id, "text": text, "agent": self.NAME}


class CodingAgent(WorkerAgent):
    NAME = "coding"
    ROUTING_PROFILE = "coding"
    SUB_PROMPT = (
        "Ты инженерный агент ACTA. Реализуй запрошенную логику. Возвращай рабочий "
        "код с краткими пояснениями."
    )

    def execute_task(self, state, task: PlanTask) -> dict:
        text = self.llm(
            f"Реализуй: {task.description}\nЯзык по умолчанию — Python.",
            mock_kind="summary",
            lang=getattr(state, "language", None),
        ).strip()
        return {"task_id": task.id, "text": text, "agent": self.NAME}


class AutomationAgent(WorkerAgent):
    NAME = "automation"
    ROUTING_PROFILE = "fast"
    SUB_PROMPT = (
        "Ты агент автоматизации ACTA. Выполни действие через доступные коннекторы "
        "интеграций, соблюдая права доступа."
    )

    def execute_task(self, state, task: PlanTask) -> dict:
        self.s.permissions.require(self.NAME, "integration.network")
        directive = state.request.metadata.get("integration")
        if directive:
            out = self.s.connectors.execute(
                directive.get("connector", "echo"),
                directive.get("action", "run"),
                directive.get("params", {}),
            )
            text = f"Интеграция выполнена: {out}"
        else:
            out = self.s.connectors.execute("echo", "dry_run", {"task": task.description})
            text = (
                "Действие подготовлено (dry-run). Для реального выполнения передайте "
                "директиву integration в metadata запроса."
            )
        return {"task_id": task.id, "text": text, "agent": self.NAME, "integration": out}


class SystemAgent(WorkerAgent):
    """Executes operating-system tasks with full host control."""

    NAME = "system"
    ROUTING_PROFILE = "fast"
    SUB_PROMPT = (
        "Ты системный агент ACTA с полным доступом к ОС. Преобразуй задачу в "
        "JSON-действие для коннектора 'system'. Доступные action: exec, spawn, "
        "processes, kill, service, fs, info. Формат: "
        '{"action": "<...>", "params": {...}}. Возвращай ТОЛЬКО JSON.'
    )

    def execute_task(self, state, task: PlanTask) -> dict:
        # SEC-8: capability + principal role gate.
        principal_role = str(state.request.metadata.get("principal_role") or "admin")
        self.s.permissions.require(self.NAME, "system.control", principal_role=principal_role)
        if not self.s.settings.allow_system_control:
            return {
                "task_id": task.id,
                "agent": self.NAME,
                "ok": False,
                "text": t("system_denied", getattr(state, "language", "ru")),
            }

        action, params = self._resolve_action(state, task)
        self.s.audit.record(self.NAME, "system_action", op=action, params=self._redact_params(params))
        out = self.s.connectors.execute("system", action, params)
        ok = bool(out.get("ok", False))
        text = self._describe(action, params, out)
        return {"task_id": task.id, "agent": self.NAME, "ok": ok, "action": action,
                "text": text, "result": out}

    # -- action resolution ------------------------------------------------- #
    def _resolve_action(self, state, task: PlanTask) -> tuple[str, dict]:
        # 1) explicit directive in request metadata wins
        directive = state.request.metadata.get("system")
        if isinstance(directive, dict) and directive.get("action"):
            return directive["action"], directive.get("params", {})

        # 2) deterministic NL parser for common operations
        parsed = self._parse_nl(task.description)
        if parsed is not None:
            return parsed

        # 3) ask the routed model for a structured JSON action (NL -> command)
        action = self._llm_action(task.description)
        if action is not None:
            return action

        # 4) safe fallback
        return "info", {}

    def _llm_action(self, description: str) -> tuple[str, dict] | None:
        """Translate a free-form instruction into a system action via the model.

        Uses the router directly so we can parse structured JSON robustly and
        give the model precise host context (the target OS).
        """
        os_name = platform.system()
        system = (
            f"{self.SUB_PROMPT}\n"
            f"Target operating system: {os_name}. Produce commands valid for this OS.\n"
            "Examples:\n"
            '  "открой калькулятор" -> {"action":"spawn","params":{"command":"open -a Calculator"}}\n'
            '  "сколько свободной памяти" -> {"action":"info","params":{}}\n'
            '  "убей процесс chrome" -> {"action":"kill","params":{"name":"chrome"}}\n'
            '  "создай файл ~/n.txt с текстом hi" -> '
            '{"action":"fs","params":{"op":"write","path":"~/n.txt","content":"hi"}}\n'
            "Return ONLY the JSON object, no prose."
            "\n[[ACTA_MOCK:system]]"
        )
        resp = self.s.router.complete(
            [ChatMessage("system", system), ChatMessage("user", description)],
            profile=self.ROUTING_PROFILE,
            temperature=0.0,
        )
        data = resp.as_json()
        if isinstance(data, dict) and data.get("action"):
            return str(data["action"]), data.get("params", {}) or {}
        return None

    def _parse_nl(self, text: str) -> tuple[str, dict] | None:
        low = text.lower()
        # System info
        if any(k in low for k in ("system info", "информация о системе", "о системе",
                                  "מידע על המערכת", "מצב המערכת")):
            return "info", {}
        # Processes
        if any(k in low for k in ("list process", "процесс", "תהליכים", "running programs")):
            return "processes", {"limit": 50}
        # Run / execute an explicit command (backticks or quotes)
        m = re.search(r"[`\"']([^`\"']{2,})[`\"']", text)
        if m and any(k in low for k in ("run", "exec", "command", "запусти", "выполни",
                                        "הרץ", "בצע", "פקודה")):
            return "exec", {"command": m.group(1)}
        return None

    def _describe(self, action: str, params: dict, out: dict) -> str:
        if not out.get("ok"):
            if out.get("code") == "confirmation_required":
                return f"[system:{action}] требуется явное подтверждение (confirm=true)"
            return f"[system:{action}] ошибка: {out.get('error', out.get('stderr', 'unknown'))}"
        if action == "info":
            return (
                f"OS={out.get('os')} {out.get('release')} · CPU={out.get('cpu_percent')}% · "
                f"user={out.get('user')} · cwd={out.get('cwd')}"
            )
        if action in ("exec", "run"):
            return f"$ {params.get('command')}\n{(out.get('stdout') or '').strip()[:1500]}"
        if action in ("processes", "list_processes"):
            return f"Процессов: {out.get('count')}"
        if action == "kill":
            return f"Остановлены PID: {out.get('killed')}"
        if action == "service":
            return f"service {params.get('name')} {params.get('op')}: rc={out.get('returncode')}"
        if action == "fs":
            return f"fs {params.get('op')} -> {out.get('path') or out.get('deleted') or 'ok'}"
        return json.dumps(out, ensure_ascii=False)[:1000]

    def _redact_params(self, params: dict) -> dict:
        redacted: dict = {}
        for key, value in params.items():
            key_low = str(key).lower()
            if key_low in {"command", "cmd", "path", "dest", "env"}:
                redacted[key] = "<redacted>"
                continue
            text = str(value)
            if len(text) > 256:
                redacted[key] = f"<truncated:{len(text)} chars>"
            else:
                redacted[key] = value
        return redacted


WORKER_AGENTS: dict[str, type[WorkerAgent]] = {
    ResearchAgent.NAME: ResearchAgent,
    CodingAgent.NAME: CodingAgent,
    AutomationAgent.NAME: AutomationAgent,
    SystemAgent.NAME: SystemAgent,
}
