"""In-process knowledge graph with persistence and relation analysis."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import networkx as nx

from acta.config import Settings, get_settings


class KnowledgeGraph:
    def __init__(self, settings: Settings | None = None, path: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self._path = path or (self.settings.ensure_data_dir() / "knowledge_graph.json")
        self._lock = threading.RLock()
        self._g = nx.MultiDiGraph()
        self._load()

    # -- persistence ------------------------------------------------------- #
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for node in data.get("nodes", []):
            nid = node.pop("id")
            self._g.add_node(nid, **node)
        for edge in data.get("edges", []):
            self._g.add_edge(edge["source"], edge["target"], key=edge.get("rel"), **edge)

    def save(self) -> None:
        with self._lock:
            data = {
                "nodes": [{"id": n, **d} for n, d in self._g.nodes(data=True)],
                "edges": [
                    {"source": u, "target": v, **d}
                    for u, v, d in self._g.edges(data=True)
                ],
            }
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- mutation ---------------------------------------------------------- #
    def upsert_entity(self, entity_id: str, *, type: str = "concept", **attrs: Any) -> str:
        with self._lock:
            if self._g.has_node(entity_id):
                self._g.nodes[entity_id].update({"type": type, **attrs})
            else:
                self._g.add_node(entity_id, type=type, **attrs)
        return entity_id

    def relate(self, source: str, target: str, rel: str = "related_to", **attrs: Any) -> None:
        with self._lock:
            for node in (source, target):
                if not self._g.has_node(node):
                    self._g.add_node(node, type="concept")
            self._g.add_edge(source, target, key=rel, rel=rel, **attrs)

    # -- query / analysis -------------------------------------------------- #
    def neighbors(self, entity_id: str, depth: int = 1) -> dict[str, Any]:
        with self._lock:
            if not self._g.has_node(entity_id):
                return {"entity": entity_id, "found": False, "related": []}
            related: list[dict[str, Any]] = []
            seen = {entity_id}
            frontier = [entity_id]
            for _ in range(depth):
                nxt = []
                for node in frontier:
                    for _, tgt, key in self._g.out_edges(node, keys=True):
                        if tgt not in seen:
                            related.append({"id": tgt, "rel": key, "direction": "out"})
                            seen.add(tgt)
                            nxt.append(tgt)
                    for src, _, key in self._g.in_edges(node, keys=True):
                        if src not in seen:
                            related.append({"id": src, "rel": key, "direction": "in"})
                            seen.add(src)
                            nxt.append(src)
                frontier = nxt
            return {"entity": entity_id, "found": True, "related": related}

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        q = query.lower()
        with self._lock:
            scored = []
            for node, data in self._g.nodes(data=True):
                hay = f"{node} {data.get('label', '')} {data.get('type', '')}".lower()
                score = sum(1 for term in q.split() if term in hay)
                if score:
                    scored.append((score, node, data))
            scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"id": node, "score": score, **data} for score, node, data in scored[:limit]
        ]

    def path_between(self, source: str, target: str) -> list[str]:
        with self._lock:
            if not (self._g.has_node(source) and self._g.has_node(target)):
                return []
            try:
                return nx.shortest_path(self._g.to_undirected(as_view=True), source, target)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return []

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entities": self._g.number_of_nodes(), "relations": self._g.number_of_edges()}
