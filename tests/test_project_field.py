"""Tests for the project tag (cross-project recall) added in 0.9.0.

Stdlib + light deps only, same contract as test_improvements: no Ollama, no
sentence-transformers, no LanceDB, so the suite stays CI-fast. Each test encodes
WHY the behaviour matters, not just that a function returns something.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "dream" / "scripts"))

import db_init
import dream_buffer

SCHEMA = Path(__file__).parent.parent / "dream" / "scripts" / "graph_schema.sql"


def _cols(db):
    c = sqlite3.connect(db)
    out = {r[1] for r in c.execute("PRAGMA table_info(nodes)").fetchall()}
    c.close()
    return out


def _indexes(db):
    c = sqlite3.connect(db)
    out = {r[1] for r in c.execute("PRAGMA index_list(nodes)").fetchall()}
    c.close()
    return out


class TestProjectMigration:
    # WHY: existing installs have a pgt.sqlite created before `project` existed.
    # CREATE TABLE IF NOT EXISTS never adds a column, so recall would silently
    # never see a project tag unless db_init backfills the column on an old DB.
    def test_fresh_db_has_project_column_and_index(self, tmp_path):
        db = tmp_path / "fresh.sqlite"
        db_init.init(db, SCHEMA)
        assert "project" in _cols(db)
        assert "idx_nodes_project" in _indexes(db)

    def test_pre_existing_db_is_migrated(self, tmp_path):
        db = tmp_path / "old.sqlite"
        old_schema = SCHEMA.read_text(encoding="utf-8").replace("    project TEXT,\n", "")
        with sqlite3.connect(db) as c:
            c.executescript(old_schema)
            c.commit()
        assert "project" not in _cols(db)  # precondition: old DB lacks the column
        db_init.init(db, SCHEMA)            # migrate
        assert "project" in _cols(db)
        assert "idx_nodes_project" in _indexes(db)

    def test_migration_is_idempotent(self, tmp_path):
        db = tmp_path / "x.sqlite"
        db_init.init(db, SCHEMA)
        db_init.init(db, SCHEMA)  # second run must not raise
        assert "project" in _cols(db)


class TestBufferCarriesProject:
    # WHY: the project tag must survive into the buffer so the nightly cycle can
    # attribute a consolidated node back to its source project.
    def test_append_event_records_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dream_buffer, "BUFFER_DIR", tmp_path)
        rec = dream_buffer.append_event(
            {"type": "fact", "content": "ship it", "project": "projet:brainmood"},
            full_llm=False,
        )
        assert rec["meta"]["project"] == "projet:brainmood"

    def test_project_defaults_to_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dream_buffer, "BUFFER_DIR", tmp_path)
        rec = dream_buffer.append_event(
            {"type": "fact", "content": "no project here"}, full_llm=False
        )
        assert rec["meta"]["project"] is None
