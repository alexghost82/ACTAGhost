from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from acta.schemas import MemoryKind, UserRequest


def test_orchestrator_handles_parallel_requests_without_exceptions(orchestrator):
    def _run(idx: int) -> str:
        response = orchestrator.run(
            UserRequest(
                user_id=f"user-{idx % 3}",
                text=f"Provide a short deterministic summary for request {idx}",
            )
        )
        return response.answer

    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(_run, range(24)))

    assert len(answers) == 24
    assert all(isinstance(answer, str) and answer.strip() for answer in answers)


def test_memory_store_supports_concurrent_writes(services):
    def _write(idx: int) -> str:
        record = services.memory.add(
            MemoryKind.EPISODIC,
            f"concurrent memory #{idx}",
            user_id="parallel-user",
        )
        return record.id

    with ThreadPoolExecutor(max_workers=10) as pool:
        ids = list(pool.map(_write, range(40)))

    assert len(ids) == 40
    assert len(set(ids)) == 40
    assert services.memory.stats(user_id="parallel-user")[MemoryKind.EPISODIC.value] == 40
