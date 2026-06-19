from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from acta.config import get_settings


def test_alembic_upgrade_creates_sqlite_schema(tmp_path, monkeypatch):
    # Probe the real (optional) alembic package submodules directly: a local
    # ``alembic/`` migrations directory exists at the repo root and would fool a
    # bare ``importorskip("alembic")`` into not skipping when alembic isn't installed.
    command = pytest.importorskip("alembic.command")
    Config = pytest.importorskip("alembic.config").Config

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("ACTA_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    command.upgrade(cfg, "head")

    db_path = get_settings().db_path
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "memories" in tables
    assert "personal" in tables
    assert "memories_fts" in tables
