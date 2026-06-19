from __future__ import annotations

from pathlib import Path

from acta.config import Settings
from acta.multimodal import MultimodalProcessor
from acta.schemas import Modality, UserRequest


class _FakeSTT:
    def transcribe(self, audio_source: str | Path) -> str | None:
        return f"heard:{Path(audio_source).name}"


class _FakeTTS:
    def synthesize(self, text: str) -> dict[str, str] | None:
        return {"path": "/tmp/fake.wav", "mime_type": "audio/wav", "backend": "fake"}


class _FakeVision:
    def describe(self, request: UserRequest, attachment: dict[str, str]) -> str | None:
        return "a red mug on a desk"


def _settings(tmp_path, **overrides) -> Settings:
    base = {"data_dir": tmp_path}
    base.update(overrides)
    return Settings(**base)


def test_stt_uses_backend_when_enabled(tmp_path):
    settings = _settings(tmp_path, multimodal_stt_enabled=True)
    processor = MultimodalProcessor(settings=settings, stt_backend=_FakeSTT())
    req = UserRequest(
        modality=Modality.VOICE,
        attachments=[{"type": "audio", "path": str(tmp_path / "sample.wav")}],
    )
    normalized = processor.normalize_input(req)
    assert "heard:sample.wav" in normalized["text"]
    assert "voice transcribed via STT" in normalized["notes"]


def test_stt_falls_back_to_provided_transcript_without_backend(tmp_path):
    settings = _settings(tmp_path, multimodal_stt_enabled=True, multimodal_stt_backend="none")
    processor = MultimodalProcessor(settings=settings)
    req = UserRequest(
        modality=Modality.VOICE,
        metadata={"transcript": "fallback transcript"},
        attachments=[{"type": "audio", "path": str(tmp_path / "sample.wav")}],
    )
    normalized = processor.normalize_input(req)
    assert "fallback transcript" in normalized["text"]


def test_tts_returns_descriptor_with_backend(tmp_path):
    settings = _settings(tmp_path, multimodal_tts_enabled=True)
    processor = MultimodalProcessor(settings=settings, tts_backend=_FakeTTS())
    rendered = processor.render_output("hello", speak=True)
    assert rendered["spoken"] is True
    assert rendered["audio"]["backend"] == "fake"


def test_tts_returns_none_when_backend_unavailable(tmp_path):
    settings = _settings(tmp_path, multimodal_tts_enabled=True, multimodal_tts_backend="none")
    processor = MultimodalProcessor(settings=settings)
    rendered = processor.render_output("hello", speak=True)
    assert rendered["spoken"] is False
    assert rendered["audio"] is None


def test_vision_describes_image_with_backend(tmp_path):
    settings = _settings(tmp_path, multimodal_vision_enabled=True)
    processor = MultimodalProcessor(settings=settings, vision_backend=_FakeVision())
    req = UserRequest(
        attachments=[{"type": "image", "caption": "fallback caption"}],
        modality=Modality.IMAGE,
    )
    normalized = processor.normalize_input(req)
    assert "[image: a red mug on a desk]" in normalized["text"]
    assert "image described via vision" in normalized["notes"]


def test_offline_defaults_keep_previous_behavior(tmp_path):
    settings = _settings(tmp_path)
    processor = MultimodalProcessor(settings=settings)
    req = UserRequest(
        text="hello",
        attachments=[{"type": "image", "caption": "my caption"}],
        modality=Modality.IMAGE,
    )
    normalized = processor.normalize_input(req)
    assert normalized["text"] == "hello\n[image: my caption]"
    assert normalized["notes"] == ["image described"]
