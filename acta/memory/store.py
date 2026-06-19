"""Encrypted SQLite-backed memory store with lexical retrieval."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acta.config import Settings, get_settings
from acta.schemas import MemoryKind
from acta.security.crypto import Crypto

_WORD = re.compile(r"[\w']+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text or "")]


@dataclass
class MemoryRecord:
    id: str
    kind: str
    content: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    user_id: str = "default"
    created_at: float = field(default_factory=time.time)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "tags": self.tags,
            "metadata": self.metadata,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "score": round(self.score, 4),
        }


class MemoryStore:
    def __init__(
        self,
        settings: Settings | None = None,
        crypto: Crypto | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.crypto = crypto or Crypto(self.settings)
        self._path = db_path or self.settings.db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    content_enc TEXT NOT NULL,
                    tokens TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind, user_id)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS personal (
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_enc TEXT NOT NULL,
                    PRIMARY KEY (user_id, key)
                )
                """
            )
            self._conn.commit()

    # -- write ------------------------------------------------------------- #
    def add(
        self,
        kind: MemoryKind | str,
        content: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        user_id: str = "default",
    ) -> MemoryRecord:
        kind_str = kind.value if isinstance(kind, MemoryKind) else str(kind)
        rec = MemoryRecord(
            id=f"mem_{uuid.uuid4().hex[:12]}",
            kind=kind_str,
            content=content,
            tags=tags or [],
            metadata=metadata or {},
            user_id=user_id,
        )
        tokens = json.dumps(_tokenize(f"{content} {' '.join(rec.tags)}"))
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?)",
                (
                    rec.id,
                    rec.kind,
                    rec.user_id,
                    self.crypto.encrypt(content),
                    tokens,
                    json.dumps(rec.tags, ensure_ascii=False),
                    json.dumps(rec.metadata, ensure_ascii=False),
                    rec.created_at,
                ),
            )
            self._conn.commit()
        return rec

    # -- read -------------------------------------------------------------- #
    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            kind=row["kind"],
            content=self.crypto.decrypt(row["content_enc"]),
            tags=json.loads(row["tags"]),
            metadata=json.loads(row["metadata"]),
            user_id=row["user_id"],
            created_at=row["created_at"],
        )

    def recent(
        self, kind: MemoryKind | str | None = None, *, user_id: str = "default", limit: int = 10
    ) -> list[MemoryRecord]:
        kind_str = kind.value if isinstance(kind, MemoryKind) else kind
        with self._lock:
            if kind_str:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE kind=? AND user_id=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (kind_str, user_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def search(
        self,
        query: str,
        *,
        kind: MemoryKind | str | None = None,
        user_id: str = "default",
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """TF-IDF-lite lexical retrieval over stored memories."""
        q_tokens = _tokenize(query)
        if not q_tokens:
            return self.recent(kind, user_id=user_id, limit=limit)
        kind_str = kind.value if isinstance(kind, MemoryKind) else kind
        with self._lock:
            if kind_str:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE kind=? AND user_id=?",
                    (kind_str, user_id),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE user_id=?", (user_id,)
                ).fetchall()
        if not rows:
            return []

        docs = [json.loads(r["tokens"]) for r in rows]
        n = len(docs)
        df: Counter[str] = Counter()
        for doc in docs:
            for tok in set(doc):
                df[tok] += 1
        q_set = set(q_tokens)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row, doc in zip(rows, docs):
            if not doc:
                continue
            tf = Counter(doc)
            score = 0.0
            for tok in q_set:
                if tok in tf:
                    idf = math.log((n + 1) / (df[tok] + 1)) + 1.0
                    score += (tf[tok] / len(doc)) * idf
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, row in scored[:limit]:
            rec = self._row_to_record(row)
            rec.score = score
            results.append(rec)
        return results

    # -- personal key/value ------------------------------------------------ #
    def set_personal(self, key: str, value: Any, *, user_id: str = "default") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO personal VALUES (?,?,?)",
                (user_id, key, self.crypto.encrypt(json.dumps(value, ensure_ascii=False))),
            )
            self._conn.commit()

    def get_personal(self, key: str, default: Any = None, *, user_id: str = "default") -> Any:
        with self._lock:
            row = self._conn.execute(
                "SELECT value_enc FROM personal WHERE user_id=? AND key=?",
                (user_id, key),
            ).fetchone()
        if not row:
            return default
        return json.loads(self.crypto.decrypt(row["value_enc"]))

    def all_personal(self, *, user_id: str = "default") -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_enc FROM personal WHERE user_id=?", (user_id,)
            ).fetchall()
        return {r["key"]: json.loads(self.crypto.decrypt(r["value_enc"])) for r in rows}

    def stats(self, *, user_id: str = "default") -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, COUNT(*) c FROM memories WHERE user_id=? GROUP BY kind",
                (user_id,),
            ).fetchall()
        return {r["kind"]: r["c"] for r in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
