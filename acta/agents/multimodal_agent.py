"""Multimodal Agent — normalize inputs and render outputs across modalities."""

from __future__ import annotations

from typing import Any

from acta.agents.base import BaseAgent
from acta.i18n import detect_language
from acta.schemas import AgentResult, SensorType
from acta.vision.frames import VisionFrame


class MultimodalAgent(BaseAgent):
    NAME = "multimodal"
    SUB_PROMPT = (
        "Обработай входные данные (текст, голос, изображения, визуальные потоки с "
        "камер и сенсоров) и сгенерируй соответствующий выход (ответ, код, уведомление)."
    )

    def normalize(self, state, result: AgentResult) -> None:
        self.s.permissions.require(self.NAME, "media.process")
        normalized = self.s.multimodal.normalize_input(state.request)
        state.normalized = normalized
        # AGENT extension: run the VLM vision pipeline for sensor frames / camera
        # requests, injecting the analysis into the normalized text.
        vision_notes = self._process_vision(state, normalized)
        # Determine the user's language (request override wins over detection).
        forced = state.request.metadata.get("language")
        default_lang = self.s.settings.default_language
        state.language = forced or detect_language(normalized.get("text", ""), default_lang)
        normalized["language"] = state.language
        result.output = normalized
        result.summary = (
            f"modality={normalized['modality']} lang={state.language} "
            f"notes={len(normalized['notes'])} vision={vision_notes}"
        )

    def _process_vision(self, state, normalized: dict[str, Any]) -> int:
        """Analyze visual sensor inputs via the VLM pipeline. Returns count."""
        if not self.s.settings.vision_enabled:
            return 0
        analyses: list[dict[str, Any]] = []
        user_id = state.request.user_id
        # 1) Explicit camera request via metadata.
        vision_meta = state.request.metadata.get("vision")
        if isinstance(vision_meta, dict) and vision_meta.get("camera_id"):
            try:
                analysis = self.s.vision.capture_and_analyze(
                    vision_meta["camera_id"],
                    vision_meta.get("instruction"),
                    user_id=user_id,
                    agent=self.NAME,
                )
                analyses.append(analysis.to_dict())
            except Exception:
                self.log.debug("camera capture/analyze failed", exc_info=True)
        # 2) Frame / sensor attachments.
        for att in state.request.attachments:
            if att.get("type") not in ("frame", "sensor"):
                continue
            try:
                frame = self._frame_from_attachment(att)
                analysis = self.s.vision.pipeline.analyze_frame(
                    frame,
                    att.get("instruction"),
                    user_id=user_id,
                    agent=self.NAME,
                )
                analyses.append(analysis.to_dict())
            except Exception:
                self.log.debug("frame analysis failed", exc_info=True)
        if not analyses:
            return 0
        for item in analyses:
            text = item["analysis"]["text"]
            normalized["text"] = f"{normalized['text']}\n[vision: {text}]".strip()
            normalized["notes"].append("visual frame analyzed via VLM")
        state.artifacts["vision"] = analyses
        return len(analyses)

    def _frame_from_attachment(self, att: dict[str, Any]) -> VisionFrame:
        sensor = att.get("sensor_type", "rgb")
        return VisionFrame(
            width=int(att.get("width", self.s.settings.vision_synthetic_width)),
            height=int(att.get("height", self.s.settings.vision_synthetic_height)),
            sensor_type=sensor if isinstance(sensor, SensorType) else SensorType(sensor),
            camera_id=att.get("camera_id", "attachment"),
            source=att.get("path") or att.get("url") or att.get("source"),
            metadata={
                k: att[k]
                for k in ("path", "url", "mime_type", "caption")
                if att.get(k) is not None
            },
        )

    def render(self, state, result: AgentResult) -> None:
        speak = state.request.modality.value == "voice" or bool(
            state.request.metadata.get("speak")
        )
        rendered = self.s.multimodal.render_output(state.answer, speak=speak)
        state.artifacts["rendered_output"] = rendered
        result.output = rendered
        result.summary = f"spoken={rendered.get('spoken', False)}"

    def handle(self, state, result: AgentResult) -> None:
        self.normalize(state, result)
