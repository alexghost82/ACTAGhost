from __future__ import annotations

from acta.agents import AgentServices
from acta.config import Settings
from acta.orchestrator import Orchestrator
from acta.schemas import MemoryKind, Modality, UserRequest


def _services(tmp_path, **overrides) -> AgentServices:
    base = {"data_dir": tmp_path, "default_provider": "mock", "vision_enabled": True}
    base.update(overrides)
    return AgentServices.build(Settings(**base))


def test_camera_connector_registered(tmp_path):
    services = _services(tmp_path)
    assert "camera" in services.connectors.names()


def test_camera_connector_full_lifecycle(tmp_path):
    services = _services(tmp_path)
    reg = services.connectors.execute(
        "camera",
        "register",
        {"name": "gate", "sensor_type": "thermal", "width": 320, "height": 240},
    )
    assert reg["ok"] is True
    cam_id = reg["camera"]["id"]

    listed = services.connectors.execute("camera", "list", {})
    assert any(c["id"] == cam_id for c in listed["cameras"])

    analyzed = services.connectors.execute(
        "camera", "analyze", {"id": cam_id, "instruction": "scan"}
    )
    assert analyzed["ok"] is True
    assert analyzed["analysis"]["analysis"]["provider"] == "mock-vlm"

    assert services.connectors.execute("camera", "disable", {"id": cam_id})["ok"] is True
    assert services.connectors.execute("camera", "enable", {"id": cam_id})["ok"] is True
    assert services.connectors.execute("camera", "remove", {"id": cam_id})["ok"] is True


def test_camera_connector_errors(tmp_path):
    services = _services(tmp_path)
    assert services.connectors.execute("camera", "capture", {"id": "missing"})["ok"] is False
    assert services.connectors.execute("camera", "bogus", {})["ok"] is False


def test_multimodal_agent_analyzes_sensor_attachment(tmp_path):
    services = _services(tmp_path)
    orch = Orchestrator(services)
    req = UserRequest(
        text="what do you see?",
        modality=Modality.SENSOR,
        attachments=[{"type": "sensor", "sensor_type": "infrared", "width": 800, "height": 600}],
    )
    resp = orch.run(req)
    assert "vision" in resp.artifacts
    assert len(resp.artifacts["vision"]) == 1
    assert services.memory.recent(MemoryKind.VISUAL, limit=5)


def test_multimodal_agent_camera_metadata_request(tmp_path):
    services = _services(tmp_path)
    spec = services.vision.cameras.add("hall", sensor_type="rgb")
    orch = Orchestrator(services)
    req = UserRequest(
        text="check the hall",
        metadata={"vision": {"camera_id": spec.id, "instruction": "any people?"}},
    )
    resp = orch.run(req)
    assert resp.artifacts.get("vision")


def test_vision_disabled_skips_pipeline(tmp_path):
    services = _services(tmp_path, vision_enabled=False)
    orch = Orchestrator(services)
    req = UserRequest(
        text="frame",
        attachments=[{"type": "sensor", "sensor_type": "rgb", "width": 100, "height": 100}],
    )
    resp = orch.run(req)
    assert "vision" not in resp.artifacts
