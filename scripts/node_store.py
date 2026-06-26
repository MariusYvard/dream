"""Single write path for creating a PGT node (SQLite + LanceDB + graph + ledger).

Before this module the only code that created base nodes was the store_event
MCP tool. The nightly consolidation wrote topic files and ledger leaves but no
nodes, so the graph that powers search_semantic, query_relations and the
CLAUDE.md index never grew from consolidation. persist_node closes that gap and
gives both callers one audited write order:

    1. vector   -> LanceDB   (if this throws, nothing else is committed)
    2. metadata -> SQLite    (single transaction)
    3. edges    -> SQLite + NetworkX graph.gpickle
    4. signed leaf -> ledger

`content` must already be sanitised: this module redacts nothing.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import pickle
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import ledger_sign

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
DB_PATH = DREAM_HOME / "pgt.sqlite"
VECTORS_DIR = DREAM_HOME / "vectors.lance"
GRAPH_PATH = DREAM_HOME / "graph.gpickle"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def persist_node(
    content: str,
    *,
    node_type: str = "fact",
    vitality: float = 0.9,
    scenario: str = "base",
    confidence: float = 0.85,
    validity_from: str | None = None,
    validity_to: str | None = None,
    source_session: str | None = None,
    relation_hints: list[dict[str, Any]] | None = None,
    ledger_op: str = "store_event",
    node_id: str | None = None,
    project: str | None = None,
    access_policy: str = "read_write",
) -> dict[str, Any]:
    """Create a node end-to-end. Returns {id, vitality, ledger}.

    relation_hints items are {parent_id, relation_type, weight?}.
    """
    from mcp_search_activation import embedder

    nid = node_id or str(uuid.uuid4())
    now = _now()
    vfrom = validity_from or now
    vto = validity_to or ""
    hints = relation_hints or []
    output_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    vec = embedder().encode(content, normalize_embeddings=True).tolist()

    # 1. Vector first — if this throws, nothing else is committed.
    import lancedb

    db = lancedb.connect(str(VECTORS_DIR))
    table = db.open_table("nodes")
    table.add([
        {
            "id": nid,
            "vector": vec,
            "content": content,
            "type": node_type,
            "vitality": vitality,
            "validity_from": vfrom,
            "validity_to": vto,
            "scenario": scenario,
        }
    ])

    # 2. Metadata + 3. edges — one transaction.
    with _conn() as conn:
        conn.execute(
            "INSERT INTO nodes (id, type, content, embedding_ref, validity_from, validity_to, confidence, "
            "vitality, source_session, project, scenario, access_policy, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                nid, node_type, content, f"lancedb:nodes:{nid}", vfrom, vto, confidence,
                vitality, source_session, project, scenario, access_policy, "active", now, now,
            ),
        )
        for hint in hints:
            if not hint.get("parent_id"):
                continue
            conn.execute(
                "INSERT INTO edges (from_id, to_id, relation_type, weight, temporal_from, scenario, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (hint["parent_id"], nid, hint.get("relation_type", "derived_from"),
                 hint.get("weight", 0.8), now, scenario, now),
            )
        conn.commit()

    # 3b. Keep the persisted NetworkX graph in sync (atomic read-modify-write).
    _sync_graph(nid, node_type, content, vitality, hints, now)

    # 4. Ledger.
    leaf = ledger_sign.append_leaf(ledger_op, nid, {"id": nid, "type": node_type, "sha": output_sha})
    return {"id": nid, "vitality": vitality, "ledger": leaf["payload_sha"][:12]}


def _sync_graph(nid: str, node_type: str, content: str, vitality: float,
                hints: list[dict[str, Any]], now: str) -> None:
    import networkx as nx

    g: nx.MultiDiGraph = nx.MultiDiGraph()
    if GRAPH_PATH.exists():
        try:
            with GRAPH_PATH.open("rb") as fh:
                g = pickle.load(fh)
        except Exception:
            g = nx.MultiDiGraph()
    g.add_node(nid, vitality=vitality, type=node_type, content=content[:240])
    for hint in hints:
        if not hint.get("parent_id"):
            continue
        g.add_edge(hint["parent_id"], nid, relation_type=hint.get("relation_type", "derived_from"),
                   weight=hint.get("weight", 0.8), temporal_from=now)
    tmp = GRAPH_PATH.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(g, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(GRAPH_PATH)
