from acta.knowledge_graph.graph import KnowledgeGraph


def test_kg_upsert_relate_search(services):
    kg = services.kg
    kg.upsert_entity("postgres", type="tech", label="PostgreSQL")
    kg.upsert_entity("pgvector", type="tech", label="pgvector")
    kg.relate("postgres", "pgvector", rel="extension")

    found = kg.search("postgres", limit=3)
    assert any(f["id"] == "postgres" for f in found)

    nbrs = kg.neighbors("postgres")
    assert nbrs["found"] is True
    assert any(r["id"] == "pgvector" for r in nbrs["related"])

    path = kg.path_between("postgres", "pgvector")
    assert path == ["postgres", "pgvector"]

    stats = kg.stats()
    assert stats["entities"] >= 2 and stats["relations"] >= 1


def test_kg_persist(services):
    kg = services.kg
    kg.upsert_entity("persist-node", type="concept")
    kg.save()
    assert services.kg._path.exists()


def test_kg_incremental_journal_reload(services):
    kg = services.kg
    kg.upsert_entity("node-a", type="tech", label="Node A")
    kg.upsert_entity("node-b", type="tech", label="Node B")
    kg.relate("node-a", "node-b", rel="depends_on")
    kg.save()

    kg.upsert_entity("node-c", type="tech", label="Node C")
    kg.relate("node-b", "node-c", rel="depends_on")
    kg.save()

    assert kg._journal_path.exists()
    reloaded = KnowledgeGraph(settings=services.settings, path=kg._path)

    assert reloaded.stats() == kg.stats()
    assert reloaded.path_between("node-a", "node-b") == ["node-a", "node-b"]
    assert any(item["id"] == "node-a" for item in reloaded.search("node", limit=5))
