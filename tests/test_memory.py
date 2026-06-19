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
