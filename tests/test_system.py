import pytest

from acta.security import PermissionDenied
from acta.integration.system import SystemConnector


def _enable_system_control(services) -> None:
    services.settings.allow_system_control = True
    services.permissions.grant("system", "system.control")


def test_system_info(services):
    _enable_system_control(services)
    conn = SystemConnector(services.settings)
    out = conn.execute("info", {})
    assert out["ok"] is True
    assert "os" in out and out["cpu_count"]


def test_system_exec_echo(services):
    _enable_system_control(services)
    conn = SystemConnector(services.settings)
    out = conn.execute("exec", {"command": "echo acta-ok", "confirm": True})
    assert out["ok"] is True
    assert "acta-ok" in out["stdout"]


def test_system_fs_lifecycle(services, tmp_path):
    _enable_system_control(services)
    conn = SystemConnector(services.settings)
    target = tmp_path / "subdir" / "file.txt"
    w = conn.execute("fs", {"op": "write", "path": str(target), "content": "hello"})
    assert w["ok"] is True
    r = conn.execute("fs", {"op": "read", "path": str(target)})
    assert r["content"] == "hello"
    d = conn.execute("fs", {"op": "delete", "path": str(target), "confirm": True})
    assert d["ok"] is True
    assert not target.exists()


def test_system_processes(services):
    _enable_system_control(services)
    conn = SystemConnector(services.settings)
    out = conn.execute("processes", {"limit": 5})
    assert out["ok"] is True
    assert out["count"] >= 1


def test_system_control_can_be_disabled(services):
    services.settings.allow_system_control = False
    conn = SystemConnector(services.settings)
    out = conn.execute("exec", {"command": "echo nope"})
    assert out["ok"] is False
    assert out.get("code") == "disabled"
    _enable_system_control(services)


def test_system_agent_routed_in_pipeline(orchestrator):
    _enable_system_control(orchestrator.s)
    from acta.schemas import UserRequest

    resp = orchestrator.run(UserRequest(text="Покажи информацию о системе"))
    # The plan should route to the system worker and execute successfully.
    system_tasks = [task for task in resp.plan.tasks if task.agent == "system"]
    assert system_tasks
    assert all(task.status.value == "done" for task in system_tasks)
    assert resp.answer


def test_system_exec_requires_confirmation(services):
    _enable_system_control(services)
    conn = SystemConnector(services.settings)
    out = conn.execute("exec", {"command": "echo no-confirm"})
    assert out["ok"] is False
    assert out["code"] == "confirmation_required"


def test_system_exec_blocks_dangerous_env_overrides(services):
    _enable_system_control(services)
    conn = SystemConnector(services.settings)
    out = conn.execute(
        "exec",
        {
            "command": ["echo", "acta-env"],
            "confirm": True,
            "env": {"PATH": "/tmp/evil", "ACTA_SAMPLE_FLAG": "1"},
        },
    )
    assert out["ok"] is True
    assert "PATH" in out["blocked_env"]


def test_system_control_denied_for_user_role_even_when_enabled(services):
    _enable_system_control(services)
    from acta.agents.specialized import SystemAgent
    from acta.orchestrator.state import PipelineState
    from acta.schemas import PlanTask, UserRequest

    state = PipelineState(
        request=UserRequest(
            user_id="alice",
            text="system info",
            metadata={"principal_role": "user"},
        )
    )
    agent = SystemAgent(services)
    with pytest.raises(PermissionDenied):
        agent.execute_task(state, PlanTask(agent="system", description="system info"))


def test_system_destructive_fs_ops_require_confirm(services, tmp_path):
    _enable_system_control(services)
    conn = SystemConnector(services.settings)
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    delete_without_confirm = conn.execute("fs", {"op": "delete", "path": str(source)})
    assert delete_without_confirm["ok"] is False
    assert delete_without_confirm["code"] == "confirmation_required"

    destination = tmp_path / "destination.txt"
    move_without_confirm = conn.execute("fs", {"op": "move", "path": str(source), "dest": str(destination)})
    assert move_without_confirm["ok"] is False
    assert move_without_confirm["code"] == "confirmation_required"
