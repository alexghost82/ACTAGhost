"""Verify behavior when a real (non-mock) model provider is connected.

A FakeProvider is injected into the AI Router to emulate a configured LLM
(OpenAI/Anthropic/Gemini/Ollama) without any network access.
"""

from __future__ import annotations

import pytest

from acta.providers.base import LLMProvider, LLMResponse
from acta.schemas import UserRequest


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self) -> None:
        super().__init__("fake-1")

    def complete(self, messages, *, temperature=0.2, max_tokens=1024) -> LLMResponse:
        system = "\n".join(m.content for m in messages if m.role == "system")
        # SystemAgent NL->command prompt
        if "Target operating system" in system:
            return LLMResponse(
                text='```json\n{"action": "spawn", "params": {"command": "echo acta-open"}}\n```',
                provider=self.name,
                model=self.model,
            )
        # Detect requested language from the respond-in directive.
        lang = "Russian"
        for name in ("Russian", "Hebrew", "English"):
            if name in system:
                lang = name
        return LLMResponse(text=f"FAKE_SYNTH[{lang}]", provider=self.name, model=self.model)


@pytest.fixture()
def real_services(services):
    """Inject a fake real provider and make the router prefer it."""
    services.router._providers["fake"] = FakeProvider()
    services.router.settings.default_provider = "fake"
    services.settings.allow_system_control = True
    services.permissions.grant("system", "system.control")
    return services


def test_router_reports_real_provider(real_services):
    assert real_services.router.real_available() is True
    assert "fake" in real_services.router.available_providers()


def test_ui_synthesizes_with_real_llm(real_services):
    from acta.orchestrator import Orchestrator

    resp = Orchestrator(real_services).run(
        UserRequest(text="Исследуй кэширование в Postgres и набросай план")
    )
    assert resp.answer.startswith("FAKE_SYNTH")
    assert "Russian" in resp.answer  # responded in the detected language


def test_ui_synthesis_respects_language(real_services):
    from acta.orchestrator import Orchestrator

    resp = Orchestrator(real_services).run(UserRequest(text="Write a plan to learn Python"))
    assert resp.language == "en"
    assert "English" in resp.answer


def test_system_agent_translates_nl_to_command(real_services):
    from acta.agents.specialized import SystemAgent
    from acta.orchestrator.state import PipelineState

    st = PipelineState(request=UserRequest(text="открой калькулятор"))
    st.normalized = {"text": st.request.text}
    from acta.schemas import PlanTask

    task = PlanTask(title="t", description="открой калькулятор", agent="system")
    out = SystemAgent(real_services).execute_task(st, task)
    assert out["action"] == "spawn"
    assert out["ok"] is True


def test_system_agent_direct_llm_action(real_services):
    """The model path returns a structured action parsed from fenced JSON."""
    from acta.agents.specialized import SystemAgent

    agent = SystemAgent(real_services)
    action = agent._llm_action("сделай что-нибудь системное")
    assert action is not None
    act, params = action
    assert act == "spawn" and "command" in params
