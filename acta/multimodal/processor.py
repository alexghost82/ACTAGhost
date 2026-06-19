"""Convert multimodal inputs into a normalized text prompt for the core."""

from __future__ import annotations

import base64
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Protocol

from acta.config import Settings, get_settings

from acta.logging_config import get_logger
from acta.providers import AIRouter
from acta.schemas import Modality, UserRequest

log = get_logger("multimodal")


class STTBackend(Protocol):
    def transcribe(self, audio_source: str | Path) -> str | None: ...


class TTSBackend(Protocol):
    def synthesize(self, text: str) -> dict[str, Any] | None: ...


class VisionBackend(Protocol):
    def describe(self, request: UserRequest, attachment: dict[str, Any]) -> str | None: ...


class PiperTTSBackend:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def synthesize(self, text: str) -> dict[str, Any] | None:
        voice = self._settings.multimodal_piper_voice
        if not voice:
            return None
        binary = self._settings.multimodal_piper_binary
        if not (shutil.which(binary) or Path(binary).exists()):
            return None
        data_dir = self._settings.ensure_data_dir() / "tts"
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
            dir=data_dir,
            prefix="piper-",
        ) as handle:
            out_path = Path(handle.name)
        cmd = [binary, "--model", voice, "--output_file", str(out_path)]
        try:
            proc = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
        except Exception:
            return None
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)
            return None
        return {"path": str(out_path), "mime_type": "audio/wav", "backend": "piper"}


class MultimodalProcessor:
    """Normalize inputs to text and prepare output renderings."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        router: AIRouter | None = None,
        stt_backend: STTBackend | None = None,
        tts_backend: TTSBackend | None = None,
        vision_backend: VisionBackend | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.router = router
        self._stt_backend = stt_backend
        self._tts_backend = tts_backend
        self._vision_backend = vision_backend

    def normalize_input(self, request: UserRequest) -> dict[str, Any]:
        """Return a dict with normalized ``text`` plus per-attachment notes."""
        text = request.text or ""
        notes: list[str] = []

        if request.modality == Modality.VOICE:
            transcript = self._transcribe(request)
            if transcript:
                text = (text + "\n" + transcript).strip()
                notes.append("voice transcribed via STT")
            else:
                notes.append("voice received (no STT backend configured)")

        for att in request.attachments:
            kind = att.get("type", "unknown")
            if kind == "image":
                caption = (
                    self._describe_image(request, att)
                    or att.get("caption")
                    or att.get("alt")
                    or "image attachment"
                )
                text = f"{text}\n[image: {caption}]".strip()
                if caption == (att.get("caption") or att.get("alt") or "image attachment"):
                    notes.append("image described")
                else:
                    notes.append("image described via vision")
            elif kind == "text":
                text = f"{text}\n{att.get('content', '')}".strip()
                notes.append("text attachment inlined")
            else:
                notes.append(f"attachment '{kind}' noted")

        return {"text": text, "notes": notes, "modality": request.modality.value}

    def render_output(self, answer: str, *, speak: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"text": answer}
        if speak:
            audio = self._synthesize(answer)
            out["audio"] = audio
            out["spoken"] = audio is not None
        return out

    # -- pluggable backends ------------------------------------------------ #
    def _transcribe(self, request: UserRequest) -> str | None:
        """Whisper/OpenAI STT with graceful fallback to provided transcript."""
        fallback = self._provided_transcript(request)
        if not self.settings.multimodal_stt_enabled:
            return fallback
        audio_source = self._extract_audio_source(request)
        if not audio_source:
            return fallback
        backend = self._stt_backend or self._load_stt_backend()
        if backend is None:
            return fallback
        try:
            transcript = backend.transcribe(audio_source)
        except Exception:
            log.debug("STT backend failed, using fallback transcript", exc_info=True)
            return fallback
        return transcript or fallback

    def _provided_transcript(self, request: UserRequest) -> str | None:
        for att in request.attachments:
            if att.get("type") == "transcript":
                return att.get("content")
        return request.metadata.get("transcript")

    def _synthesize(self, text: str) -> dict[str, Any] | None:
        """Piper TTS synthesis (optional, disabled by default)."""
        if not self.settings.multimodal_tts_enabled:
            log.debug("TTS disabled; returning text only")
            return None
        backend = self._tts_backend or self._load_tts_backend()
        if backend is None:
            log.debug("TTS backend not available; returning text only")
            return None
        try:
            return backend.synthesize(text)
        except Exception:
            log.debug("TTS backend failed; returning text only", exc_info=True)
            return None

    def _describe_image(self, request: UserRequest, attachment: dict[str, Any]) -> str | None:
        if not self.settings.multimodal_vision_enabled:
            return None
        backend = self._vision_backend
        if backend is not None:
            try:
                return backend.describe(request, attachment)
            except Exception:
                return None
        if self.settings.multimodal_vision_backend not in (None, "", "router"):
            return None
        if self.router is None:
            return None
        image_source = self._extract_image_source(attachment)
        if not image_source:
            return None
        return self.router.describe_image(
            image_source,
            mime_type=attachment.get("mime_type"),
            prompt=self.settings.multimodal_vision_prompt,
            profile="multimodal",
            max_tokens=256,
        )

    def _load_stt_backend(self) -> STTBackend | None:
        backend_name = (self.settings.multimodal_stt_backend or "auto").lower()
        if backend_name in {"faster-whisper", "auto"}:
            backend = self._load_faster_whisper_backend()
            if backend is not None:
                return backend
        if backend_name in {"whisper", "openai-whisper", "auto"}:
            backend = self._load_openai_whisper_backend()
            if backend is not None:
                return backend
        if backend_name in {"openai", "openai-api", "auto"}:
            backend = self._load_openai_audio_backend()
            if backend is not None:
                return backend
        return None

    def _load_faster_whisper_backend(self) -> STTBackend | None:
        try:
            from faster_whisper import WhisperModel
        except Exception:
            return None

        model = WhisperModel(self.settings.multimodal_whisper_model)

        class _Backend:
            def transcribe(self, audio_source: str | Path) -> str | None:
                segments, _ = model.transcribe(str(audio_source))
                text = " ".join(seg.text.strip() for seg in segments if seg.text).strip()
                return text or None

        return _Backend()

    def _load_openai_whisper_backend(self) -> STTBackend | None:
        try:
            import whisper
        except Exception:
            return None
        model = whisper.load_model(self.settings.multimodal_whisper_model)

        class _Backend:
            def transcribe(self, audio_source: str | Path) -> str | None:
                result = model.transcribe(str(audio_source))
                text = result.get("text", "").strip()
                return text or None

        return _Backend()

    def _load_openai_audio_backend(self) -> STTBackend | None:
        if not self.settings.openai_api_key:
            return None
        try:
            from openai import OpenAI
        except Exception:
            return None
        client = OpenAI(api_key=self.settings.openai_api_key)
        model_name = self.settings.multimodal_openai_stt_model

        class _Backend:
            def transcribe(self, audio_source: str | Path) -> str | None:
                with Path(audio_source).open("rb") as fh:
                    resp = client.audio.transcriptions.create(model=model_name, file=fh)
                text = getattr(resp, "text", "") or ""
                return text.strip() or None

        return _Backend()

    def _load_tts_backend(self) -> TTSBackend | None:
        backend_name = (self.settings.multimodal_tts_backend or "").lower()
        if backend_name in {"piper", "auto"}:
            return PiperTTSBackend(self.settings)
        return None

    def _extract_audio_source(self, request: UserRequest) -> str | Path | None:
        for att in request.attachments:
            if att.get("type") in {"audio", "voice"}:
                for key in ("path", "file_path", "url"):
                    if att.get(key):
                        return str(att[key])
        for key in ("audio_path", "voice_path"):
            if request.metadata.get(key):
                return str(request.metadata[key])
        return None

    def _extract_image_source(self, attachment: dict[str, Any]) -> str | Path | None:
        for key in ("path", "file_path", "url"):
            if attachment.get(key):
                return str(attachment[key])
        if attachment.get("base64"):
            image_b64 = attachment["base64"]
            with tempfile.NamedTemporaryFile(
                suffix=".img",
                delete=False,
                dir=self.settings.ensure_data_dir(),
                prefix="vision-",
            ) as tmp:
                tmp.write(base64.b64decode(image_b64))
                return tmp.name
        return None
