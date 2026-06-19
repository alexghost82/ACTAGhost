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
