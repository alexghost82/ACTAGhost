"""Multimodal Agent — normalize inputs and render outputs across modalities."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.i18n import detect_language
from acta.schemas import AgentResult


class MultimodalAgent(BaseAgent):
    NAME = "multimodal"
    SUB_PROMPT = (
        "Обработай входные данные (текст, голос, изображения) и сгенерируй "
        "соответствующий выход (ответ, код, уведомление)."
    )

    def normalize(self, state, result: AgentResult) -> None:
        self.s.permissions.require(self.NAME, "media.process")
        normalized = self.s.multimodal.normalize_input(state.request)
        state.normalized = normalized
        # Determine the user's language (request override wins over detection).
        forced = state.request.metadata.get("language")
        default_lang = self.s.settings.default_language
        state.language = forced or detect_language(normalized.get("text", ""), default_lang)
        normalized["language"] = state.language
        result.output = normalized
        result.summary = (
            f"modality={normalized['modality']} lang={state.language} "
            f"notes={len(normalized['notes'])}"
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
