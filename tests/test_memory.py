import json
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from acta.schemas import MemoryKind


def test_add_and_recent(services):
    mem = services.memory
    mem.add(MemoryKind.EPISODIC, "пользователь спросил про Postgres")
    mem.add(MemoryKind.EPISODIC, "пользователь спросил про Redis")
    recent = mem.recent(MemoryKind.EPISODIC, limit=10)
    assert len(recent) == 2
    assert recent[0].content.endswith("Redis")


def test_search_relevance(services):
    mem = services.memory
    mem.add(MemoryKind.SEMANTIC, "Postgres поддерживает индексы и репликацию")
    mem.add(MemoryKind.SEMANTIC, "Кошки любят спать днём")
    hits = mem.search("postgres индексы", kind=MemoryKind.SEMANTIC, limit=2)
    assert hits
    assert "Postgres" in hits[0].content
    assert hits[0].score > 0


def test_personal_kv_encrypted(services):
    mem = services.memory
    mem.set_personal("profile", {"goals": ["learn rust"]})
    assert mem.get_personal("profile")["goals"] == ["learn rust"]


def test_content_is_encrypted_on_disk(services):
    mem = services.memory
    mem.add(MemoryKind.SEMANTIC, "СОВЕРШЕННО_СЕКРЕТНО_МАРКЕР")
    raw = services.settings.db_path.read_bytes()
    assert b"\xd0\xa1\xd0\x9e\xd0\x92" not in raw  # cyrillic bytes absent
    assert b"MARKER" not in raw


def _legacy_rank(rows, query: str, limit: int) -> list[tuple[float, str]]:
    q_tokens = query.lower().split()
    docs = [json.loads(row["tokens"]) for row in rows]
    n_docs = len(docs)
    if not n_docs:
        return []
    df: Counter[str] = Counter()
    for doc in docs:
        for token in set(doc):
            df[token] += 1
    scored: list[tuple[float, str]] = []
    for row, doc in zip(rows, docs):
        tf = Counter(doc)
        score = 0.0
        for token in set(q_tokens):
            if token in tf:
                idf = math.log((n_docs + 1) / (df[token] + 1)) + 1.0
                score += (tf[token] / len(doc)) * idf
        if score > 0:
            scored.append((score, row["id"]))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def test_search_fts_matches_legacy_top_hit(services):
    mem = services.memory
    mem.add(MemoryKind.SEMANTIC, "postgres indexing guide", tags=["database"])
    mem.add(MemoryKind.SEMANTIC, "redis pubsub patterns", tags=["queue"])
    mem.add(MemoryKind.SEMANTIC, "postgres replication and indexing", tags=["database"])
    mem.add(MemoryKind.SEMANTIC, "sqlite wal mode", tags=["database"])

    query = "postgres indexing"
    conn = mem._get_conn()
    rows = conn.execute("SELECT id, tokens FROM memories WHERE user_id=?", ("default",)).fetchall()
    legacy_rank = _legacy_rank(rows, query, limit=2)
    hits = mem.search(query, kind=MemoryKind.SEMANTIC, limit=2)

    assert hits
    assert legacy_rank
    assert hits[0].id == legacy_rank[0][1]
    assert hits[0].score > 0


def test_concurrent_writes_use_wal_mode(services):
    mem = services.memory

    def writer(thread_idx: int) -> None:
        for offset in range(20):
            mem.add(
                MemoryKind.EPISODIC,
                f"thread-{thread_idx}-memory-{offset}",
                user_id=f"user-{thread_idx % 2}",
            )

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(writer, idx) for idx in range(6)]
        for future in futures:
            future.result()

    assert sum(mem.stats(user_id="user-0").values()) == 60
    assert sum(mem.stats(user_id="user-1").values()) == 60
    mode = mem._get_conn().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
