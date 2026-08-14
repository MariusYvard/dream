"""Apply the SQLite schema (graph_schema.sql) to pgt.sqlite.

stdlib-only, idempotent (every statement is CREATE ... IF NOT EXISTS). Safe to
run before the dream deps are installed and safe to run repeatedly. This
replaces the previous dependency on the `sqlite3` command-line binary, which is
not present by default on Windows.

    python db_init.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
DB_PATH = DREAM_HOME / "pgt.sqlite"
SCHEMA_PATH = Path(__file__).parent / "graph_schema.sql"


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column/index additions for DBs created before a field existed.

    CREATE TABLE IF NOT EXISTS never adds a column to an existing table, so a
    pre-existing pgt.sqlite would miss `project`. Add it in code, guarded by a
    PRAGMA check so the call stays idempotent and safe to run repeatedly.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)").fetchall()}
    if cols and "project" not in cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN project TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project)")


def init(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> Path:
    """Create the schema in `db_path`. Returns the database path."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
        _migrate(conn)
        conn.commit()
    return db_path


if __name__ == "__main__":
    try:
        path = init()
        print(f"[db_init] schema applied to {path}")
    except Exception as exc:
        print(f"[db_init] FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
