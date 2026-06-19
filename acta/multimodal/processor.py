"""Convert multimodal inputs into a normalized text prompt for the core."""

from __future__ import annotations

from typing import Any

from acta.logging_config import get_logger
from acta.schemas import Modality, UserRequest

log = get_logger("multimodal")


class MultimodalProcessor:
    """Normalize inputs to text and prepare output renderings."""

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
                caption = att.get("caption") or att.get("alt") or "image attachment"
                text = f"{text}\n[image: {caption}]".strip()
                notes.append("image described")
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
        """Hook for Whisper STT. Returns provided transcript if present."""
        for att in request.attachments:
            if att.get("type") == "transcript":
                return att.get("content")
        return request.metadata.get("transcript")

    def _synthesize(self, text: str) -> dict[str, Any] | None:
        """Hook for Piper TTS. Returns audio descriptor when a backend exists."""
        log.debug("TTS backend not configured; returning text only")
        return None
