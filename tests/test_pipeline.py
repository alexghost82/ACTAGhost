from acta.schemas import MemoryKind, Modality, UserRequest


def test_full_pipeline_runs(orchestrator):
    req = UserRequest(text="Исследуй лучшие практики кэширования в Postgres и набросай план")
    resp = orchestrator.run(req)

    assert resp.answer
    assert resp.intent is not None
    assert resp.plan is not None and len(resp.plan.tasks) >= 1
    assert resp.strategy is not None
    # Every core stage should appear in the trace.
    agents_in_trace = {t.agent for t in resp.trace}
    for expected in {"intent", "context", "reasoning", "planning", "decision", "security", "ui"}:
        assert expected in agents_in_trace, f"missing {expected} in trace"
    # No failed steps.
    assert all(t.ok for t in resp.trace), [t for t in resp.trace if not t.ok]


def test_pipeline_persists_memory(orchestrator, services):
    before = services.memory.stats().get(MemoryKind.EPISODIC.value, 0)
    orchestrator.run(UserRequest(text="Запомни, что я работаю над проектом ACTA"))
    after = services.memory.stats().get(MemoryKind.EPISODIC.value, 0)
    assert after == before + 1


def test_pipeline_grows_knowledge_graph(orchestrator, services):
    before = services.kg.stats()["entities"]
    orchestrator.run(UserRequest(text='Создай заметку про "Knowledge Graph"'))
    after = services.kg.stats()["entities"]
    assert after > before


def test_pipeline_handles_voice_modality(orchestrator):
    req = UserRequest(
        text="",
        modality=Modality.VOICE,
        metadata={"transcript": "Какая погода влияет на продуктивность?"},
    )
    resp = orchestrator.run(req)
    assert resp.answer
    assert resp.intent.summary


def test_pipeline_detects_language(orchestrator):
    he = orchestrator.run(UserRequest(text="כתוב תוכנית ללימוד פייתון"))
    assert he.language == "he"
    en = orchestrator.run(UserRequest(text="Write a plan to learn Python"))
    assert en.language == "en"
    ru = orchestrator.run(UserRequest(text="Напиши план изучения Python"))
    assert ru.language == "ru"


def test_language_override_metadata(orchestrator):
    resp = orchestrator.run(UserRequest(text="hello", metadata={"language": "he"}))
    assert resp.language == "he"


def test_integration_directive_executes(orchestrator):
    req = UserRequest(
        text="Отправь данные через api",
        metadata={
            "integration": {
                "connector": "fs",
                "action": "write",
                "params": {"path": "note.txt", "content": "hello acta"},
            }
        },
    )
    resp = orchestrator.run(req)
    assert resp.artifacts.get("integration", {}).get("ok") is True
