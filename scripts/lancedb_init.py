"""Initialise the LanceDB vector store used by the PGT.

Run once at install or whenever the data layer must be rebuilt.

    python lancedb_init.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import lancedb
import pyarrow as pa

import model_profile
EMBED_DIM = model_profile.embed_dim()  # profile-driven (bge-m3 1024, bge-small 384)

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
VECTORS_DIR = DREAM_HOME / "vectors.lance"

SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
        pa.field("content", pa.string()),
        pa.field("type", pa.string()),
        pa.field("vitality", pa.float32()),
        pa.field("validity_from", pa.string()),
        pa.field("validity_to", pa.string()),
        pa.field("scenario", pa.string()),
    ]
)


def init() -> None:
    DREAM_HOME.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(VECTORS_DIR))
    if "nodes" in db.table_names():
        print(f"[lancedb_init] table 'nodes' already exists at {VECTORS_DIR}")
        return
    db.create_table("nodes", schema=SCHEMA)
    print(f"[lancedb_init] created table 'nodes' at {VECTORS_DIR}")


if __name__ == "__main__":
    try:
        init()
    except Exception as exc:
        print(f"[lancedb_init] FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
