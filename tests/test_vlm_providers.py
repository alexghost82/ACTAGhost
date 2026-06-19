from __future__ import annotations

import pytest

from acta.config import Settings
from acta.providers.vlm import (
    CloudVLMProvider,
    FallbackVLMProvider,
    LocalVLMProvider,
    MockVLMProvider,
    QuantizationConfig,
    VLMProvider,
    VLMRequest,
    build_vlm_provider,
)
from acta.schemas import SensorType
from acta.vision.frames import VisionFrame


def _frame(**kw) -> VisionFrame:
    base = dict(width=640, height=480, sensor_type=SensorType.THERMAL, camera_id="c1")
    base.update(kw)
    return VisionFrame(**base)


def test_quantization_config():
    q = QuantizationConfig("int4")
    assert q.bits == 4
    assert q.memory_factor == pytest.approx(0.125)
    assert QuantizationConfig("none").bits == 32
    with pytest.raises(ValueError):
        QuantizationConfig("int3")


def test_mock_vlm_is_deterministic():
    provider = MockVLMProvider()
    frame = _frame(camera_id="cam", source=None)
    frame.frame_id = "fixed-frame"
    r1 = provider.analyze(VLMRequest(frame=frame, instruction="find heat"))
    r2 = provider.analyze(VLMRequest(frame=frame, instruction="find heat"))
    assert r1.text == r2.text
    assert r1.objects == r2.objects
    assert 0.0 < r1.confidence <= 1.0
    assert "thermal" in r1.text
    assert r1.to_dict()["provider"] == "mock-vlm"


def test_mock_vlm_varies_with_instruction():
    provider = MockVLMProvider()
    frame = _frame()
    frame.frame_id = "f"
    a = provider.analyze(VLMRequest(frame=frame, instruction="A"))
    b = provider.analyze(VLMRequest(frame=frame, instruction="B"))
    assert a.text != b.text


class _StubRouter:
    def __init__(self, available: bool, description: str | None) -> None:
        self._available = available
        self._desc = description

    def real_available(self) -> bool:
        return self._available

    def describe_image(self, *a, **k) -> str | None:
        return self._desc


def test_cloud_vlm_requires_source():
    router = _StubRouter(True, "a cat")
    provider = CloudVLMProvider(router)
    assert provider.is_available() is True
    assert provider.analyze(VLMRequest(frame=_frame(source=None))) is None
    res = provider.analyze(VLMRequest(frame=_frame(source="/tmp/x.jpg")))
    assert res is not None and res.text == "a cat"


def test_cloud_vlm_handles_empty_router_response():
    provider = CloudVLMProvider(_StubRouter(True, None))
    assert provider.analyze(VLMRequest(frame=_frame(source="/tmp/x.jpg"))) is None


def test_local_vlm_offline_returns_none():
    provider = LocalVLMProvider("http://127.0.0.1:1", "llava")
    assert provider.is_available() is False
    assert provider.analyze(VLMRequest(frame=_frame(source=None))) is None


class _AlwaysNone(VLMProvider):
    name = "none1"

    def analyze(self, request):
        return None


class _Boom(VLMProvider):
    name = "boom"

    def analyze(self, request):
        raise RuntimeError("kaboom")


def test_fallback_chain_returns_first_success():
    chain = FallbackVLMProvider([_AlwaysNone(), _Boom(), MockVLMProvider()])
    assert chain.is_available() is True
    result = chain.analyze(VLMRequest(frame=_frame()))
    assert result is not None and result.provider == "mock-vlm"


def test_fallback_requires_providers():
    with pytest.raises(ValueError):
        FallbackVLMProvider([])


def test_build_vlm_provider_strategies():
    assert isinstance(build_vlm_provider(Settings(vlm_provider="mock")), MockVLMProvider)
    assert isinstance(build_vlm_provider(Settings(vlm_provider="local")), FallbackVLMProvider)
    assert isinstance(build_vlm_provider(Settings(vlm_provider="cloud")), MockVLMProvider)
    assert isinstance(
        build_vlm_provider(Settings(vlm_provider="cloud"), _StubRouter(True, "x")),
        FallbackVLMProvider,
    )
    assert isinstance(build_vlm_provider(Settings(vlm_provider="auto")), FallbackVLMProvider)
