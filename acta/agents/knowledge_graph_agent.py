"""Knowledge Graph Agent — search relations and grow the graph."""

from __future__ import annotations

from acta.agents.base import BaseAgent
from acta.schemas import AgentResult


class KnowledgeGraphAgent(BaseAgent):
    NAME = "knowledge_graph"
    SUB_PROMPT = (
        "Обрабатывай запросы на поиск и анализ связей в Knowledge Graph, "
        "предоставляй структурированные данные."
    )

    def handle(self, state, result: AgentResult) -> None:
        self.s.permissions.require(self.NAME, "kg.read")
        kg = self.s.kg
        text = state.normalized.get("text") or state.request.text

        findings = kg.search(text, limit=6)
        state.kg_findings = findings

        # Grow the graph: connect the request to its entities and intent.
        self.s.permissions.require(self.NAME, "kg.write")
        req_node = kg.upsert_entity(
            state.request.request_id, type="request", label=text[:60]
        )
        intent_node = kg.upsert_entity(
            f"intent:{state.intent.type.value}", type="intent", label=state.intent.type.value
        )
        kg.relate(req_node, intent_node, rel="has_intent")
        for ent in state.intent.entities:
            node = kg.upsert_entity(f"entity:{ent}", type="entity", label=ent)
            kg.relate(req_node, node, rel="mentions")
        kg.save()

        result.output = {"findings": findings, "stats": kg.stats()}
        result.summary = f"kg_hits={len(findings)} {kg.stats()}"
