from acta.integration.system import SystemConnector


def test_system_info(services):
    conn = SystemConnector(services.settings)
    out = conn.execute("info", {})
    assert out["ok"] is True
    assert "os" in out and out["cpu_count"]


def test_system_exec_echo(services):
    conn = SystemConnector(services.settings)
    out = conn.execute("exec", {"command": "echo acta-ok"})
    assert out["ok"] is True
    assert "acta-ok" in out["stdout"]


def test_system_fs_lifecycle(services, tmp_path):
    conn = SystemConnector(services.settings)
    target = tmp_path / "subdir" / "file.txt"
    w = conn.execute("fs", {"op": "write", "path": str(target), "content": "hello"})
    assert w["ok"] is True
    r = conn.execute("fs", {"op": "read", "path": str(target)})
    assert r["content"] == "hello"
    d = conn.execute("fs", {"op": "delete", "path": str(target)})
    assert d["ok"] is True
    assert not target.exists()


def test_system_processes(services):
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
    services.settings.allow_system_control = True


def test_system_agent_routed_in_pipeline(orchestrator):
    from acta.schemas import UserRequest

    resp = orchestrator.run(UserRequest(text="Покажи информацию о системе"))
    # The plan should route to the system worker and execute successfully.
    system_tasks = [task for task in resp.plan.tasks if task.agent == "system"]
    assert system_tasks
    assert all(task.status.value == "done" for task in system_tasks)
    assert resp.answer
