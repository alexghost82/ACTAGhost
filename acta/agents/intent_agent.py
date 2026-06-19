"""Intent Agent — analyse the request and determine the user's intent."""

from __future__ import annotations

import re

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult, Intent, IntentType

_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is",
    "по", "и", "в", "на", "для", "с", "что", "как", "это", "мне", "я",
}

_INTENT_CUES: dict[IntentType, tuple[str, ...]] = {
    IntentType.RESEARCH: ("research", "find", "investigate", "compare", "исследуй", "найди",
                          "сравни", "חקור", "מצא", "השווה"),
    IntentType.AUTOMATION: ("automate", "schedule", "every", "автоматизируй", "расписани",
                            "каждый", "אוטומציה", "תזמן", "כל"),
    IntentType.COMMAND: ("create", "delete", "send", "deploy", "run", "open", "kill", "stop",
                         "создай", "удали", "отправь", "запусти", "открой", "останови",
                         "צור", "מחק", "שלח", "הרץ", "פתח", "עצור"),
    IntentType.TASK: ("build", "implement", "write", "make", "plan", "сделай", "построй",
                      "напиши", "реализуй", "בנה", "כתוב", "תכנן", "עשה"),
    IntentType.QUESTION: ("what", "why", "how", "when", "who", "?", "что", "почему", "как",
                          "когда", "מה", "למה", "איך", "מתי", "מי"),
}

_EXTERNAL_CUES = ("http", "api", "github", "email", "send", "fetch", "url", "webhook", "отправь", "запрос")


class IntentAgent(BaseAgent):
    NAME = "intent"
    ROUTING_PROFILE = "fast"
    SUB_PROMPT = (
        "Определи намерение пользователя на основе входного текста, учитывая "
        "контекст и историю взаимодействий. Выведи структурированное описание "
        "намерения."
    )

    def handle(self, state, result: AgentResult) -> None:
        text = (state.normalized.get("text") or state.request.text or "").strip()
        lowered = text.lower()

        scores: dict[IntentType, int] = {}
        for itype, cues in _INTENT_CUES.items():
            scores[itype] = sum(1 for c in cues if c in lowered)
        best_type = max(scores, key=lambda intent_type: scores[intent_type])
        if scores[best_type] == 0:
            best_type = IntentType.SMALL_TALK if len(lowered) < 12 else IntentType.TASK

        entities = self._entities(text)
        objectives = self._objectives(text)
        requires_external = any(c in lowered for c in _EXTERNAL_CUES)
        total_cues = sum(scores.values())
        confidence = min(0.95, 0.45 + 0.12 * total_cues + (0.1 if entities else 0))

        intent = Intent(
            type=best_type,
            summary=self._summary(text, best_type),
            objectives=objectives,
            entities=entities,
            constraints=self._constraints(text),
            requires_external=requires_external,
            confidence=round(confidence, 2),
        )
        # Natural-language sharpening via the routed model (offline-safe).
        intent.summary = self.llm(
            f"Кратко опиши намерение пользователя одной фразой.\nЗапрос: {text}",
            mock_kind="summary",
            lang=getattr(state, "language", None),
        ).strip() or intent.summary

        state.intent = intent
        result.output = intent.model_dump()
        result.summary = f"intent={intent.type.value} conf={intent.confidence}"

    # -- heuristics -------------------------------------------------------- #
    def _entities(self, text: str) -> list[str]:
        # Capitalized words, quoted strings and URLs make decent lightweight entities.
        ents = re.findall(r"\"([^\"]+)\"|'([^']+)'", text)
        flat = [a or b for a, b in ents]
        flat += re.findall(r"https?://\S+", text)
        flat += [w for w in re.findall(r"\b[A-ZА-Я][a-zа-я]{2,}\b", text)]
        seen, out = set(), []
        for e in flat:
            if e.lower() not in seen:
                seen.add(e.lower())
                out.append(e)
        return out[:8]

    def _objectives(self, text: str) -> list[str]:
        parts = re.split(r"[.\n;]|(?:\bи\b)|(?:\band\b)", text)
        objs = [p.strip() for p in parts if len(p.strip()) > 3]
        return objs[:5] or ([text] if text else [])

    def _constraints(self, text: str) -> list[str]:
        cons = []
        for m in re.finditer(r"(без |не |only |must |должно |нужно )([^.,\n]{3,40})", text, re.I):
            cons.append(m.group(0).strip())
        return cons[:5]

    def _summary(self, text: str, itype: IntentType) -> str:
        head = text.strip().split("\n")[0][:120]
        return f"{itype.value}: {head}" if head else itype.value
