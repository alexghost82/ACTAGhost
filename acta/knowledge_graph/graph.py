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
        self._journal_path = self._path.with_suffix(".journal")
        self._lock = threading.RLock()
        self._g = nx.MultiDiGraph()
        self._search_index: dict[str, set[str]] = {}
        self._node_terms: dict[str, set[str]] = {}
        self._pending_ops: list[dict[str, Any]] = []
        self._ops_since_compaction = 0
        self._compact_every = max(1, int(self.settings.kg_compact_every_ops))
        self._compact_journal_bytes = max(4 * 1024, int(self.settings.kg_compact_journal_bytes))
        self._load()

    # -- persistence ------------------------------------------------------- #
    @staticmethod
    def _terms_for_node(entity_id: str, data: dict[str, Any]) -> set[str]:
        terms = set()
        for value in (entity_id, data.get("label", ""), data.get("type", "")):
            text = str(value or "").strip().lower()
            if text:
                terms.update(text.split())
        return terms

    def _index_node(self, entity_id: str) -> None:
        data = dict(self._g.nodes[entity_id])
        old_terms = self._node_terms.get(entity_id, set())
        for term in old_terms:
            bucket = self._search_index.get(term)
            if not bucket:
                continue
            bucket.discard(entity_id)
            if not bucket:
                self._search_index.pop(term, None)
        new_terms = self._terms_for_node(entity_id, data)
        self._node_terms[entity_id] = new_terms
        for term in new_terms:
            self._search_index.setdefault(term, set()).add(entity_id)

    def _apply_upsert(self, entity_id: str, *, type: str, attrs: dict[str, Any]) -> None:
        if self._g.has_node(entity_id):
            self._g.nodes[entity_id].update({"type": type, **attrs})
        else:
            self._g.add_node(entity_id, type=type, **attrs)
        self._index_node(entity_id)

    def _apply_relate(self, source: str, target: str, rel: str, attrs: dict[str, Any]) -> None:
        for node in (source, target):
            if not self._g.has_node(node):
                self._g.add_node(node, type="concept")
                self._index_node(node)
        self._g.add_edge(source, target, key=rel, rel=rel, **attrs)

    def _record_mutation(self, op: dict[str, Any]) -> None:
        self._pending_ops.append(op)

    def _compact_snapshot(self) -> None:
        data = {
            "nodes": [{"id": n, **d} for n, d in self._g.nodes(data=True)],
            "edges": [{"source": u, "target": v, **d} for u, v, d in self._g.edges(data=True)],
        }
        tmp_path = self._path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)
        try:
            self._journal_path.unlink()
        except FileNotFoundError:
            pass
        self._ops_since_compaction = 0

    def _load(self) -> None:
        if not self._path.exists():
            data = {}
        else:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        for node in data.get("nodes", []):
            node_copy = dict(node)
            nid = node_copy.pop("id")
            self._g.add_node(nid, **node_copy)
            self._index_node(nid)
        for edge in data.get("edges", []):
            self._g.add_edge(edge["source"], edge["target"], key=edge.get("rel"), **edge)
        if not self._journal_path.exists():
            return
        try:
            for line in self._journal_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                op = json.loads(line)
                if op.get("op") == "upsert":
                    self._apply_upsert(
                        op["id"],
                        type=op.get("type", "concept"),
                        attrs=dict(op.get("attrs", {})),
                    )
                elif op.get("op") == "relate":
                    self._apply_relate(
                        op["source"],
                        op["target"],
                        rel=op.get("rel", "related_to"),
                        attrs=dict(op.get("attrs", {})),
                    )
        except (json.JSONDecodeError, OSError):
            # Corrupted journal can be discarded on next compaction.
            return

    def save(self) -> None:
        with self._lock:
            if not self._pending_ops:
                return
            self._journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self._journal_path.open("a", encoding="utf-8") as handle:
                for op in self._pending_ops:
                    handle.write(json.dumps(op, ensure_ascii=False))
                    handle.write("\n")
            self._ops_since_compaction += len(self._pending_ops)
            self._pending_ops.clear()
            if not self._path.exists():
                self._compact_snapshot()
                return
            if (
                self._ops_since_compaction >= self._compact_every
                or (
                    self._journal_path.exists()
                    and self._journal_path.stat().st_size >= self._compact_journal_bytes
                )
            ):
                self._compact_snapshot()

    # -- mutation ---------------------------------------------------------- #
    def upsert_entity(self, entity_id: str, *, type: str = "concept", **attrs: Any) -> str:
        with self._lock:
            self._apply_upsert(entity_id, type=type, attrs=attrs)
            self._record_mutation({"op": "upsert", "id": entity_id, "type": type, "attrs": attrs})
        return entity_id

    def relate(self, source: str, target: str, rel: str = "related_to", **attrs: Any) -> None:
        with self._lock:
            self._apply_relate(source, target, rel=rel, attrs=attrs)
            self._record_mutation(
                {"op": "relate", "source": source, "target": target, "rel": rel, "attrs": attrs}
            )

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
        terms = [term for term in q.split() if term]
        with self._lock:
            candidates: set[str] = set()
            for term in terms:
                candidates.update(self._search_index.get(term, set()))
            if not candidates:
                candidates = set(self._g.nodes())
            scored = []
            for node in candidates:
                data = dict(self._g.nodes[node])
                hay = f"{node} {data.get('label', '')} {data.get('type', '')}".lower()
                score = sum(1 for term in terms if term in hay)
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
