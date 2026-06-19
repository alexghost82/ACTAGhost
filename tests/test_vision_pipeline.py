from __future__ import annotations

import pytest

from acta.config import Settings
from acta.memory import MemoryStore
from acta.schemas import MemoryKind, SensorType
from acta.security import AuditLog, PermissionRegistry
from acta.security.permissions import PermissionDenied
from acta.vision.cameras import CameraRegistry
from acta.vision.frames import CameraSpec, VisionFrame
from acta.vision.pipeline import EventBus, VisionEvent, VisionPipeline, VisionService
from acta.providers.vlm import MockVLMProvider
from acta.vision.preprocess import PreprocessConfig


def _settings(tmp_path, **overrides) -> Settings:
    base = {"data_dir": tmp_path, "vision_enabled": True, "vlm_provider": "mock"}
    base.update(overrides)
    return Settings(**base)


def test_camera_spec_validation():
    with pytest.raises(ValueError):
        CameraSpec(width=0)
    with pytest.raises(ValueError):
        CameraSpec(fps=0)
    spec = CameraSpec(name="c", sensor_type="thermal")
    assert spec.sensor_type is SensorType.THERMAL
    assert spec.channels == 1


def test_registry_add_capture_synthetic():
    reg = CameraRegistry()
    spec = reg.add("door", sensor_type="infrared", width=320, height=240)
    assert reg.get(spec.id) is spec
    frame = reg.capture(spec.id, sequence=2)
    assert frame.width == 320 and frame.sensor_type is SensorType.INFRARED
    assert frame.metadata["source_kind"] == "synthetic"
    assert reg.capture(spec.id, sequence=2).metadata["scene_seed"] == frame.metadata["scene_seed"]


def test_registry_capture_file_source(tmp_path):
    img = tmp_path / "frame.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    reg = CameraRegistry()
    spec = reg.add("file-cam", source=str(img))
    frame = reg.capture(spec.id)
    assert frame.source == str(img)
    assert frame.metadata["source_kind"] == "file"


def test_registry_errors_and_toggle():
    reg = CameraRegistry()
    with pytest.raises(KeyError):
        reg.capture("nope")
    spec = reg.add("c")
    assert reg.set_enabled(spec.id, False) is True
    assert reg.set_enabled("missing", False) is False
    with pytest.raises(RuntimeError):
        reg.capture(spec.id)
    assert reg.remove(spec.id) is True
    assert reg.remove(spec.id) is False


def test_event_bus_publish_subscribe():
    bus = EventBus()
    seen = []
    bus.subscribe("x", lambda e: seen.append(e.frame_id))

    def boom(_e):
        raise RuntimeError("subscriber error")

    bus.subscribe("x", boom)
    bus.publish(VisionEvent("x", "f1"))
    assert seen == ["f1"]
    assert len(bus.history) == 1


def _pipeline(tmp_path, **kw):
    memory = MemoryStore(_settings(tmp_path))
    perms = PermissionRegistry()
    audit = AuditLog(_settings(tmp_path))
    pipe = VisionPipeline(
        vlm_provider=MockVLMProvider(),
        config=PreprocessConfig(patch_size=448, max_patches=6),
        memory=memory,
        audit=audit,
        permissions=perms,
        **kw,
    )
    return pipe, memory


def test_pipeline_analyze_persists_and_emits(tmp_path):
    pipe, memory = _pipeline(tmp_path)
    events = []
    pipe.bus.subscribe("frame_analyzed", lambda e: events.append(e))
    frame = VisionFrame(width=640, height=480, sensor_type=SensorType.THERMAL, camera_id="cam")
    analysis = pipe.analyze_frame(frame, "find people", user_id="alice")
    assert analysis.visual_tokens > 0
    assert analysis.result.provider == "mock-vlm"
    assert analysis.persisted_id is not None
    assert events and events[0].payload["provider"] == "mock-vlm"
    recs = memory.recent(MemoryKind.VISUAL, user_id="alice")
    assert recs and "thermal" in recs[0].tags


def test_pipeline_permission_denied(tmp_path):
    pipe, _ = _pipeline(tmp_path)
    frame = VisionFrame(width=100, height=100, camera_id="c")
    with pytest.raises(PermissionDenied):
        pipe.analyze_frame(frame, agent="ui")


def test_pipeline_no_persist(tmp_path):
    pipe, memory = _pipeline(tmp_path)
    frame = VisionFrame(width=200, height=200, camera_id="c")
    analysis = pipe.analyze_frame(frame, persist=False)
    assert analysis.persisted_id is None


class _NullProvider(MockVLMProvider):
    def analyze(self, request):
        return None


def test_pipeline_degrades_when_provider_returns_none(tmp_path):
    memory = MemoryStore(_settings(tmp_path))
    pipe = VisionPipeline(
        vlm_provider=_NullProvider(),
        config=PreprocessConfig(),
        memory=memory,
        permissions=PermissionRegistry(),
    )
    frame = VisionFrame(width=100, height=100, camera_id="c")
    analysis = pipe.analyze_frame(frame)
    assert analysis.result.provider == "none"
    assert "no analysis available" in analysis.result.text


def test_vision_service_build_and_capture(tmp_path):
    settings = _settings(tmp_path)
    memory = MemoryStore(settings)
    service = VisionService.build(
        settings, memory=memory, audit=AuditLog(settings), permissions=PermissionRegistry()
    )
    assert service.enabled is True
    spec = service.cameras.add("c", sensor_type="depth")
    analysis = service.capture_and_analyze(spec.id, "describe", agent="integration")
    assert analysis.visual_tokens > 0
    assert analysis.to_dict()["analysis"]["provider"] == "mock-vlm"
    assert isinstance(service.bus, EventBus)
