"""Agent Orchestrator.

Owns the lifecycle of all agents and drives the ACTA cognitive pipeline. The
flow follows the specified order and dependencies: intent → context → reasoning
→ planning → decision → execution → memory → knowledge graph → integration →
security → multimodal → UI. Worker tasks run sequentially or in parallel
according to the Decision Agent's strategy, always respecting task dependencies.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from acta.agents import (
    AgentServices,
    ContextAgent,
    DecisionAgent,
    IntegrationAgent,
    IntentAgent,
    KnowledgeGraphAgent,
    MemoryAgent,
    MultimodalAgent,
    PlanningAgent,
    ReasoningAgent,
    SecurityAgent,
    UIAgent,
)
from acta.agents.specialized import WORKER_AGENTS, WorkerAgent
from acta.logging_config import get_logger
from acta.orchestrator.state import PipelineState
from acta.schemas import ActaResponse, AgentResult, IntentType, PlanTask, TaskStatus, UserRequest

log = get_logger("orchestrator")


class Orchestrator:
    SUB_PROMPT = (
        "Организуй работу агентов, контролируй прогресс, обеспечь коммуникацию и "
        "интеграцию результатов."
    )

    def __init__(self, services: AgentServices | None = None) -> None:
        self.s = services or AgentServices.build()
        # Cognitive core + cross-cutting agents.
        self.multimodal = MultimodalAgent(self.s)
        self.intent = IntentAgent(self.s)
        self.memory = MemoryAgent(self.s)
        self.kg = KnowledgeGraphAgent(self.s)
        self.context = ContextAgent(self.s)
        self.reasoning = ReasoningAgent(self.s)
        self.planning = PlanningAgent(self.s)
        self.decision = DecisionAgent(self.s)
        self.integration = IntegrationAgent(self.s)
        self.security = SecurityAgent(self.s)
        self.ui = UIAgent(self.s)
        # Worker pool.
        self.workers: dict[str, WorkerAgent] = {
            name: cls(self.s) for name, cls in WORKER_AGENTS.items()
        }

    # ------------------------------------------------------------------ #
    def run(self, request: UserRequest) -> ActaResponse:
        state = PipelineState(request=request)
        self.s.audit.record("orchestrator", "request_received", request_id=request.request_id)
        step = 0

        # 1) UI Agent intake (acknowledge + normalize via multimodal).
        step += 1
        intake = AgentResult(agent="ui").done()
        intake.summary = "request received"
        state.add_trace(step, intake)

        # 2) Multimodal: normalize inputs to text.
        step = self._step(step, self.multimodal.run, state, runner=self.multimodal.normalize)

        # PERF-6 / ROADMAP P1#14: short-circuit trivial greetings.
        if self._is_small_talk_fast_path(state):
            step += 1
            intent_result = AgentResult(agent="intent").done()
            intent_result.summary = "small-talk fast path"
            intent_result.output = {"intent": IntentType.SMALL_TALK.value}
            state.intent.type = IntentType.SMALL_TALK
            state.intent.summary = "trivial greeting"
            state.intent.confidence = 1.0
            state.answer = self._small_talk_answer(state.language)
            state.add_trace(step, intent_result)

            # Keep post-processing and observability wrappers active.
            step = self._step(step, self.memory.run, state, runner=self.memory.persist)
            step = self._step(step, self.multimodal.run, state, runner=self.multimodal.render)
            self.s.audit.record("orchestrator", "request_complete", request_id=request.request_id)
            return state.to_response()

        # 3) Intent Agent.
        step = self._step(step, self.intent.run, state)

        # 4) Memory Agent: retrieve relevant memories.
        step = self._step(step, self.memory.run, state, runner=self.memory.retrieve)

        # 5) Knowledge Graph Agent: search + grow graph.
        step = self._step(step, self.kg.run, state)

        # 6) Context Agent: update dynamic user model.
        step = self._step(step, self.context.run, state)

        # 7) Reasoning Agent.
        step = self._step(step, self.reasoning.run, state)

        # 8) Planning Agent.
        step = self._step(step, self.planning.run, state)

        # 9) Decision Agent: strategy + routing.
        step = self._step(step, self.decision.run, state)

        # 10) Execute the plan via worker agents (the orchestration core).
        step += 1
        exec_result = self._execute_plan(state)
        state.add_trace(step, exec_result)

        # 11) Integration Agent (explicit external interaction check).
        step = self._step(step, self.integration.run, state)

        # 12) Security Agent: enforce permissions, verify encryption, audit.
        step = self._step(step, self.security.run, state)

        # 13) UI Agent: compose the consolidated answer.
        step = self._step(step, self.ui.run, state)

        # 14) Memory Agent: persist the interaction (episodic + semantic).
        step = self._step(step, self.memory.run, state, runner=self.memory.persist)

        # 15) Multimodal Agent: render output for the requested modality.
        step = self._step(step, self.multimodal.run, state, runner=self.multimodal.render)

        self.s.audit.record("orchestrator", "request_complete", request_id=request.request_id)
        return state.to_response()

    def _is_small_talk_fast_path(self, state: PipelineState) -> bool:
        text = (state.normalized.get("text") or state.request.text or "").strip().lower()
        if not text or len(text) > 32:
            return False
        normalized = "".join(ch for ch in text if ch.isalnum() or ch.isspace()).strip()
        if not normalized:
            return False
        greetings = {
            "hi",
            "hello",
            "hey",
            "yo",
            "sup",
            "привет",
            "здравствуй",
            "здравствуйте",
            "хай",
            "shalom",
            "שלום",
            "היי",
            "הי",
        }
        return normalized in greetings

    def _small_talk_answer(self, lang: str) -> str:
        if lang == "ru":
            return "Привет! Я на связи. Чем могу помочь?"
        if lang == "he":
            return "היי! אני כאן. איך אפשר לעזור?"
        return "Hey! I'm here and ready to help."

    # ------------------------------------------------------------------ #
    def _step(self, step: int, run_fn, state: PipelineState, runner=None) -> int:
        step += 1
        if runner is not None:
            # Agents exposing multiple phases (memory/multimodal) take a runner.
            result = self._run_phase(runner, state)
        else:
            result = run_fn(state)
        state.add_trace(step, result)
        return step

    def _run_phase(self, phase_callable, state: PipelineState) -> AgentResult:
        """Wrap a sub-phase (e.g. memory.retrieve) into the audited run cycle."""
        # Determine the owning agent name from the bound method's instance.
        agent = getattr(phase_callable.__self__, "NAME", "agent")
        result = AgentResult(agent=agent)
        self.s.audit.record(agent, "start", phase=phase_callable.__name__)
        try:
            phase_callable(state, result)
        except Exception as exc:
            result.ok = False
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("phase %s failed", phase_callable.__name__)
        result.done()
        state.results[f"{agent}:{phase_callable.__name__}"] = result
        self.s.audit.record(agent, "finish", phase=phase_callable.__name__, ok=result.ok)
        return result

    # ------------------------------------------------------------------ #
    def _execute_plan(self, state: PipelineState) -> AgentResult:
        result = AgentResult(agent="orchestrator")
        tasks = state.plan.tasks
        if not tasks:
            result.summary = "no tasks"
            return result.done()

        parallel = state.strategy.name == "parallel"
        completed: set[str] = set()
        executed = 0

        # Process tasks respecting dependencies; run ready independent tasks
        # together when the strategy allows parallelism.
        remaining = list(tasks)
        guard = 0
        while remaining and guard < len(tasks) + 5:
            guard += 1
            ready = [t for t in remaining if all(d in completed for d in t.depends_on)]
            if not ready:
                # Dependency cycle / dangling dep: run the rest sequentially.
                ready = remaining
            if parallel and len(ready) > 1:
                self._run_tasks_parallel(state, ready)
            else:
                for task in ready:
                    self._run_single_task(state, task)
            for task in ready:
                completed.add(task.id)
                executed += 1
            remaining = [t for t in remaining if t.id not in completed]

        failed = [t.id for t in tasks if t.status == TaskStatus.FAILED]
        result.ok = not failed
        result.output = {
            "executed": executed,
            "failed": failed,
            "strategy": state.strategy.name,
        }
        result.summary = f"executed={executed} failed={len(failed)} ({state.strategy.name})"
        return result.done()

    def _run_tasks_parallel(self, state: PipelineState, tasks: Iterable[PlanTask]) -> None:
        tasks = list(tasks)
        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
            pool.map(lambda t: self._run_single_task(state, t), tasks)

    def _run_single_task(self, state: PipelineState, task: PlanTask) -> None:
        worker = self.workers.get(task.agent) or self.workers["research"]
        task.status = TaskStatus.RUNNING
        try:
            self.s.permissions.require("orchestrator", "agent.invoke")
            task.result = worker.execute_task(state, task)
            task.status = TaskStatus.DONE
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.result = {"error": f"{type(exc).__name__}: {exc}"}
            log.exception("task %s (%s) failed", task.id, task.agent)
        self.s.audit.record(
            "orchestrator", "task_done", task_id=task.id, agent=task.agent, status=task.status.value
        )
