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
        self._search_backend = self.settings.memory_search_backend.lower()
        self._local = threading.local()
        self._conn_guard = threading.RLock()
        self._connections: set[sqlite3.Connection] = set()
        self._fts_enabled = False
        conn = self._create_connection()
        try:
            self._init_schema(conn)
        finally:
            conn.close()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = self._create_connection()
        self._local.conn = conn
        with self._conn_guard:
            self._connections.add(conn)
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind, user_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS personal (
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_enc TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            )
            """
        )
        if self._search_backend != "legacy":
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                    USING fts5(memory_id UNINDEXED, user_id UNINDEXED, kind UNINDEXED, tokens)
                    """
                )
                self._fts_enabled = True
                self._rebuild_fts_index(conn)
            except sqlite3.OperationalError:
                self._fts_enabled = False
        conn.commit()

    def _tokens_json_to_text(self, raw: str) -> str:
        try:
            tokens = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        if not isinstance(tokens, list):
            return ""
        return " ".join(str(tok) for tok in tokens if tok)

    def _rebuild_fts_index(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM memories_fts")
        rows = conn.execute("SELECT id, user_id, kind, tokens FROM memories").fetchall()
        payload = [
            (row["id"], row["user_id"], row["kind"], self._tokens_json_to_text(row["tokens"]))
            for row in rows
        ]
        if payload:
            conn.executemany(
                "INSERT INTO memories_fts(memory_id, user_id, kind, tokens) VALUES (?,?,?,?)",
                payload,
            )

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
        token_list = _tokenize(f"{content} {' '.join(rec.tags)}")
        tokens = json.dumps(token_list)
        conn = self._get_conn()
        conn.execute(
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
        if self._fts_enabled:
            conn.execute(
                "INSERT INTO memories_fts(memory_id, user_id, kind, tokens) VALUES (?,?,?,?)",
                (rec.id, rec.user_id, rec.kind, " ".join(token_list)),
            )
        conn.commit()
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
        conn = self._get_conn()
        if kind_str:
            rows = conn.execute(
                "SELECT * FROM memories WHERE kind=? AND user_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (kind_str, user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def _fetch_rows_for_fallback(
        self, kind_str: str | None, *, user_id: str
    ) -> list[sqlite3.Row]:
        conn = self._get_conn()
        if kind_str:
            return conn.execute(
                "SELECT * FROM memories WHERE kind=? AND user_id=?",
                (kind_str, user_id),
            ).fetchall()
        return conn.execute("SELECT * FROM memories WHERE user_id=?", (user_id,)).fetchall()

    def _search_candidates_fts(
        self, q_tokens: list[str], *, kind_str: str | None, user_id: str, limit: int
    ) -> list[sqlite3.Row]:
        candidate_limit = max(limit * 20, 50)
        query_terms = []
        for token in q_tokens:
            if token:
                query_terms.append(f'"{token.replace(chr(34), chr(34) * 2)}"')
        if not query_terms:
            return []
        fts_query = " OR ".join(query_terms)
        sql = (
            "SELECT m.* FROM memories_fts f "
            "JOIN memories m ON m.id=f.memory_id "
            "WHERE f.user_id=? AND f.tokens MATCH ?"
        )
        params: list[Any] = [user_id, fts_query]
        if kind_str:
            sql += " AND f.kind=?"
            params.append(kind_str)
        sql += " ORDER BY bm25(memories_fts) LIMIT ?"
        params.append(candidate_limit)
        return self._get_conn().execute(sql, params).fetchall()

    def _rank_rows_tfidf(
        self, rows: list[sqlite3.Row], q_tokens: list[str], *, limit: int
    ) -> list[MemoryRecord]:
        docs = [json.loads(r["tokens"]) for r in rows]
        n = len(docs)
        if n == 0:
            return []
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
        if self._fts_enabled:
            try:
                rows = self._search_candidates_fts(
                    q_tokens, kind_str=kind_str, user_id=user_id, limit=limit
                )
            except sqlite3.OperationalError:
                self._fts_enabled = False
                rows = self._fetch_rows_for_fallback(kind_str, user_id=user_id)
        else:
            rows = self._fetch_rows_for_fallback(kind_str, user_id=user_id)
        if not rows:
            return []
        return self._rank_rows_tfidf(rows, q_tokens, limit=limit)

    # -- personal key/value ------------------------------------------------ #
    def set_personal(self, key: str, value: Any, *, user_id: str = "default") -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO personal VALUES (?,?,?)",
            (user_id, key, self.crypto.encrypt(json.dumps(value, ensure_ascii=False))),
        )
        conn.commit()

    def get_personal(self, key: str, default: Any = None, *, user_id: str = "default") -> Any:
        row = self._get_conn().execute(
            "SELECT value_enc FROM personal WHERE user_id=? AND key=?",
            (user_id, key),
        ).fetchone()
        if not row:
            return default
        return json.loads(self.crypto.decrypt(row["value_enc"]))

    def all_personal(self, *, user_id: str = "default") -> dict[str, Any]:
        rows = self._get_conn().execute(
            "SELECT key, value_enc FROM personal WHERE user_id=?", (user_id,)
        ).fetchall()
        return {r["key"]: json.loads(self.crypto.decrypt(r["value_enc"])) for r in rows}

    def stats(self, *, user_id: str = "default") -> dict[str, int]:
        rows = self._get_conn().execute(
            "SELECT kind, COUNT(*) c FROM memories WHERE user_id=? GROUP BY kind",
            (user_id,),
        ).fetchall()
        return {r["kind"]: r["c"] for r in rows}

    def close(self) -> None:
        local_conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if local_conn is not None:
            with self._conn_guard:
                self._connections.discard(local_conn)
            local_conn.close()
            self._local.conn = None
        with self._conn_guard:
            for conn in list(self._connections):
                conn.close()
            self._connections.clear()
