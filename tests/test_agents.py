from acta.orchestrator.state import PipelineState
from acta.schemas import IntentType, UserRequest


def _state(text: str) -> PipelineState:
    return PipelineState(request=UserRequest(text=text))


def test_intent_agent_classifies_research(services):
    from acta.agents import IntentAgent

    st = _state("Исследуй и сравни базы данных Postgres и MySQL")
    st.normalized = {"text": st.request.text}
    res = IntentAgent(services).run(st)
    assert res.ok
    assert st.intent.type == IntentType.RESEARCH
    assert st.intent.confidence > 0


def test_planning_creates_tasks(services):
    from acta.agents import IntentAgent, PlanningAgent

    st = _state("Сделай план и напиши код парсера, затем отправь результат")
    st.normalized = {"text": st.request.text}
    IntentAgent(services).run(st)
    PlanningAgent(services).run(st)
    assert len(st.plan.tasks) >= 1
    agents = {t.agent for t in st.plan.tasks}
    assert agents.issubset({"research", "coding", "automation"})


def test_decision_assigns_and_routes(services):
    from acta.agents import DecisionAgent, IntentAgent, PlanningAgent

    st = _state("Исследуй тему и подготовь отчёт")
    st.normalized = {"text": st.request.text}
    IntentAgent(services).run(st)
    PlanningAgent(services).run(st)
    DecisionAgent(services).run(st)
    assert st.strategy.name in ("sequential", "parallel")
    assert set(st.strategy.assignments) == {t.id for t in st.plan.tasks}
    assert set(st.strategy.model_routing) == {t.id for t in st.plan.tasks}


def test_security_agent_verifies_encryption(services):
    from acta.agents import DecisionAgent, IntentAgent, PlanningAgent, SecurityAgent

    st = _state("Сделай что-нибудь полезное")
    st.normalized = {"text": st.request.text}
    IntentAgent(services).run(st)
    PlanningAgent(services).run(st)
    DecisionAgent(services).run(st)
    res = SecurityAgent(services).run(st)
    assert res.ok
    assert st.artifacts["security"]["encryption_ok"] is True
