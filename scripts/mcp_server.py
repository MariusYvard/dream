"""FastMCP server exposing the dream stack to Claude.

Tools exposed:
    - store_event
    - search_semantic
    - query_relations
    - update_vitality
    - propose_counterfactual
    - sanitize_local
    - load_context
    - health_check
    - set_mode
    - verify_counterfactual

Run as stdio for MCP host integration:
    python mcp_server.py

Smoke test:
    python mcp_server.py --smoke
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pickle
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# NOTE: heavy modules (mcp_search_activation → sentence-transformers, lancedb,
# numpy, networkx, vitality_engine, load_context, counterfactual_garden) are
# imported lazily inside the tools that need them. This keeps `--smoke` and the
# tool-registry import path free of the multi-GB ML stack.
import cache_layer
import circuit_breaker
import ledger_sign
import prometheus_metrics
from dream_buffer import append_event
from sanitize_local import sanitize as sanitize_text

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
DB_PATH = DREAM_HOME / "pgt.sqlite"
GRAPH_PATH = DREAM_HOME / "graph.gpickle"

mcp = FastMCP("dream")

# In-process graph cache — kept in sync with disk after each store_event.
_GRAPH = None  # networkx.MultiDiGraph, lazily built on first use


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ── Graph persistence helpers ──────────────────────────────────────────────────

def _load_graph():
    global _GRAPH
    if _GRAPH is None:
        if GRAPH_PATH.exists():
            with GRAPH_PATH.open("rb") as fh:
                _GRAPH = pickle.load(fh)
        else:
            import networkx as nx

            _GRAPH = nx.MultiDiGraph()
    return _GRAPH


def _flush_graph() -> None:
    """Atomically persist the in-process graph to disk."""
    global _GRAPH
    if _GRAPH is None:
        return
    tmp = GRAPH_PATH.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(_GRAPH, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(GRAPH_PATH)


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def store_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a sanitized event into the PGT (LanceDB + SQLite + graph).

    Write ordering is deliberate: the vector lands in LanceDB FIRST, then the
    SQLite metadata is committed, then the graph is synced. If the vector write
    fails we abort before committing any SQLite row, so we never leave metadata
    pointing at a vector that does not exist.
    """
    import lancedb

    from mcp_search_activation import embedder

    record = append_event(payload)
    vec = embedder().encode(record["content"], normalize_embeddings=True).tolist()
    nid = record["id"]
    now = _now()
    scenario = payload.get("scenario", "base")
    relation_hints = payload.get("relation_hints") or []

    # 1. Vector first — if this throws, nothing has been committed yet.
    db = lancedb.connect(str(DREAM_HOME / "vectors.lance"))
    table = db.open_table("nodes")
    table.add(
        [
            {
                "id": nid,
                "vector": vec,
                "content": record["content"],
                "type": record["type"],
                "vitality": 0.9,
                "validity_from": record["validity"]["from"],
                "validity_to": record["validity"]["to"] or "",
                "scenario": scenario,
            }
        ]
    )

    # 2. SQLite metadata — single transaction, committed only once.
    with _conn() as conn:
        conn.execute(
            "INSERT INTO nodes (id, type, content, embedding_ref, validity_from, validity_to, confidence, "
            "vitality, source_session, scenario, access_policy, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                nid,
                record["type"],
                record["content"],
                f"lancedb:nodes:{nid}",
                record["validity"]["from"],
                record["validity"]["to"],
                record["validity"]["confidence"],
                0.9,
                (record.get("meta") or {}).get("source_session"),
                scenario,
                payload.get("access_policy", "read_write"),
                "active",
                now,
                now,
            ),
        )
        for hint in relation_hints:
            conn.execute(
                "INSERT INTO edges (from_id, to_id, relation_type, weight, temporal_from, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (hint["parent_id"], nid, hint["relation_type"], hint.get("weight", 0.8), now, now),
            )
        conn.commit()

    # 3. Keep NetworkX in sync so spreading_activation is never stale.
    g = _load_graph()
    g.add_node(nid, vitality=0.9, type=record["type"], content=record["content"][:240])
    for hint in relation_hints:
        g.add_edge(
            hint["parent_id"],
            nid,
            relation_type=hint["relation_type"],
            weight=hint.get("weight", 0.8),
            temporal_from=now,
        )
    _flush_graph()

    leaf = ledger_sign.append_leaf(
        "store_event", nid, {"id": nid, "type": record["type"], "sha": record["meta"]["output_sha"]}
    )
    return {"status": "ok", "id": nid, "vitality": 0.9, "ledger": leaf["payload_sha"][:12]}


@mcp.tool()
def search_semantic(query: str, k: int = 25, vitality_min: float = 0.3, rerank: bool = True) -> dict[str, Any]:
    """Hybrid retrieval (BM25 + dense + activation + cross-encoder)."""
    import time

    from mcp_search_activation import hybrid_search

    start = time.perf_counter()
    hits = hybrid_search(query=query, k=k, vitality_min=vitality_min, rerank=rerank)
    prometheus_metrics.record_search_latency((time.perf_counter() - start) * 1000.0)
    return {"hits": [h.__dict__ for h in hits]}


@mcp.tool()
def query_relations(seed_ids: list[str], max_depth: int = 3, decay: float = 0.7) -> dict[str, Any]:
    """Run spreading activation on the graph from the seed nodes."""
    from mcp_search_activation import spreading_activation

    return {"scores": spreading_activation(seed_ids, max_depth=max_depth, decay=decay)}


@mcp.tool()
def update_vitality(node_ids: list[str]) -> dict[str, Any]:
    """Recompute vitality for the given nodes and persist the new value."""
    if not node_ids:
        return {"updated": {}}

    import numpy as np

    from mcp_search_activation import embedder
    from vitality_engine import VitalityInputs, compute as vitality_compute

    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, content, vitality, access_count, last_accessed FROM nodes WHERE id IN ({','.join(['?']*len(node_ids))})",
            node_ids,
        ).fetchall()

    embs = embedder().encode([r["content"] for r in rows], normalize_embeddings=True)
    goals_vec = _active_goals_vector()

    new_vitality: dict[str, float] = {}
    with _conn() as conn:
        for r, emb in zip(rows, embs):
            vi = VitalityInputs(
                last_accessed=dt.datetime.fromisoformat(r["last_accessed"]) if r["last_accessed"] else None,
                access_count=int(r["access_count"]),
                co_activation_score=0.0,
                node_embedding=np.asarray(emb, dtype=float),
                goals_embedding=goals_vec,
                contradiction_weight=_contradiction_weight(r["id"], conn),
            )
            v = vitality_compute(vi)
            conn.execute("UPDATE nodes SET vitality = ?, updated_at = ? WHERE id = ?", (v, _now(), r["id"]))
            new_vitality[r["id"]] = v
        conn.commit()
    return {"updated": new_vitality}


@mcp.tool()
def propose_counterfactual(seed_id: str) -> dict[str, Any]:
    """Grow the Counterfactual Garden from a seed node."""
    import counterfactual_garden

    with _conn() as conn:
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (seed_id,)).fetchone()
        if row is None:
            return {"status": "seed_not_found"}
        seed = dict(row)
        neighbours = [
            dict(r)
            for r in conn.execute(
                "SELECT n.id, n.content, n.type FROM edges e JOIN nodes n ON n.id = e.to_id WHERE e.from_id = ? LIMIT 12",
                (seed_id,),
            ).fetchall()
        ]

    branches = counterfactual_garden.generate_garden(seed, neighbours)
    payloads = counterfactual_garden.materialise(seed_id, branches)
    out_ids: list[str] = []
    now = _now()
    with _conn() as conn:
        for p in payloads:
            conn.execute(
                "INSERT INTO nodes (id, type, content, embedding_ref, validity_from, validity_to, confidence, "
                "vitality, scenario, access_policy, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    p["id"], p["type"], p["content"], f"lancedb:nodes:{p['id']}",
                    p["validity"]["from"], p["validity"]["to"], p["validity"]["confidence"],
                    p["validity"]["confidence"], p["scenario"], p["access_policy"], "active", now, now,
                ),
            )
            edge = p["edge"]
            conn.execute(
                "INSERT INTO edges (from_id, to_id, relation_type, weight, temporal_from, scenario, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'counterfactual', ?)",
                (edge["from"], edge["to"], edge["relation_type"], edge["weight"], edge["temporal_from"], now),
            )
            out_ids.append(p["id"])
        conn.commit()
    return {"status": "ok", "branch_ids": out_ids, "count": len(out_ids)}


@mcp.tool()
def sanitize_local(content: str) -> dict[str, Any]:
    """Run the local sanitisation pipeline and return the redacted text."""
    r = sanitize_text(content)
    prometheus_metrics.record_sanitise_latency(r.runtime_ms)
    return {
        "text": r.text,
        "replacements": r.replacements,
        "model": r.model,
        "runtime_ms": r.runtime_ms,
        "input_sha": r.input_sha,
        "output_sha": r.output_sha,
    }


@mcp.tool()
def load_context(goal_text: str, token_budget: int = 2000, vitality_min: float = 0.5, topics_exclude: list[str] | None = None) -> dict[str, Any]:
    """Assemble a working-memory bundle for the current session."""
    from load_context import build_bundle

    return build_bundle(goal_text=goal_text, token_budget=token_budget, vitality_min=vitality_min, topics_exclude=topics_exclude)


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Return a snapshot of the dream stack health."""
    with _conn() as conn:
        vit_row = conn.execute("SELECT AVG(vitality) FROM nodes WHERE status = 'active'").fetchone()
        hitl_row = conn.execute("SELECT COUNT(*) FROM hitl_queue WHERE resolved_at IS NULL").fetchone()

    ledger_ok = ledger_sign.verify()
    vitality_avg = float(vit_row[0] or 0.0)
    hitl_pending = int(hitl_row[0] or 0)

    from ollama_health import ollama_up

    ollama_reachable = ollama_up()

    # Live RAM of the running MCP server, retained as a peak in the cache so the
    # SECURISE>15Go trigger reflects the long-lived process, not a cold start.
    ram_peak = float(cache_layer.get("metric:ram_peak_mb") or 0.0)
    try:
        import psutil

        rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
        ram_peak = max(ram_peak, rss_mb)
        cache_layer.set("metric:ram_peak_mb", ram_peak, ttl=86400)
    except Exception:
        pass  # psutil absent: fall back to the cached peak (or 0.0)

    snapshot = circuit_breaker.HealthProbe(
        latency_p95_ms=float(cache_layer.get("metric:latency_p95") or 0.0),
        consensus_rate_24h=float(cache_layer.get("metric:consensus_rate_24h") or 1.0),
        vitality_avg=vitality_avg,
        ram_peak_mb=ram_peak,
        ledger_merkle_ok=ledger_ok,
    )
    state = circuit_breaker.evaluate(snapshot)

    # Refresh the gauges so /metrics reflects the latest probe.
    _MODE_CODE = {"NORMAL": 0, "CONSERVATEUR": 1, "SECURISE": 2}
    prometheus_metrics.VITALITY_AVG.set(vitality_avg)
    prometheus_metrics.HITL_PENDING.set(hitl_pending)
    prometheus_metrics.RAM_PEAK.set(ram_peak)
    prometheus_metrics.LEDGER_OK.set(1 if ledger_ok else 0)
    prometheus_metrics.CIRCUIT_MODE.set(_MODE_CODE.get(state.mode, 0))
    prometheus_metrics.OLLAMA_UP.set(1 if ollama_reachable else 0)

    return {
        "mode": state.mode,
        "green_streak": state.green_streak,
        "vitality_avg": vitality_avg,
        "ledger_merkle_ok": ledger_ok,
        "hitl_pending": hitl_pending,
        "ollama_up": ollama_reachable,
        "cache_backend": cache_layer.backend(),
        "updated_at": state.updated_at,
    }


@mcp.tool()
def set_mode(mode: str) -> dict[str, Any]:
    """Manually override the circuit breaker mode. NORMAL | CONSERVATEUR | SECURISE."""
    # FIX #8: use the public API instead of calling _save() directly.
    return circuit_breaker.force_mode(mode)


@mcp.tool()
def verify_counterfactual(branch_id: str) -> dict[str, Any]:
    """Compare predicted_outcome against the recent event window. Promote, decay or prune."""
    from mcp_search_activation import hybrid_search

    with _conn() as conn:
        row = conn.execute(
            "SELECT id, content, validity_from, validity_to, vitality FROM nodes WHERE id = ?",
            (branch_id,),
        ).fetchone()
        if row is None:
            return {"status": "branch_not_found"}

    pred = row["content"]
    # FIX #6: removed unused `window = embedder().encode([], ...)` that was here.
    hits = hybrid_search(query=pred, k=10, vitality_min=0.4, rerank=False)
    if not hits:
        return {"status": "prune", "match_score": 0.0}
    match = max(h.cosine for h in hits)

    with _conn() as conn:
        if match >= 0.75:
            conn.execute(
                "UPDATE nodes SET scenario = 'base', type = 'process', access_policy = 'read_write' WHERE id = ?",
                (branch_id,),
            )
            decision = "promote"
        elif match >= 0.5:
            new_conf = float(row["vitality"]) * 0.7
            conn.execute("UPDATE nodes SET confidence = ? WHERE id = ?", (new_conf, branch_id))
            decision = "decay"
        else:
            conn.execute("UPDATE nodes SET status = 'archived' WHERE id = ?", (branch_id,))
            decision = "prune"
        conn.commit()
    return {"status": "ok", "decision": decision, "match_score": match}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _active_goals_vector():
    import numpy as np

    from mcp_search_activation import embedder

    with _conn() as conn:
        rows = conn.execute(
            "SELECT content FROM nodes WHERE type = 'decision' AND vitality > 0.7 ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
    if not rows:
        return None
    embs = embedder().encode([r["content"] for r in rows], normalize_embeddings=True)
    return np.mean(embs, axis=0)


def _contradiction_weight(node_id: str, conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(weight), 0) FROM edges WHERE to_id = ? AND relation_type = 'contradicts'",
        (node_id,),
    ).fetchone()
    return float(row[0] or 0.0)


def _smoke() -> None:
    """List registered tool names via the public FastMCP API (no private attrs)."""
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    print(json.dumps({"tools": [t.name for t in tools]}, indent=2))


def _ensure_schema() -> None:
    """Idempotently apply the SQLite schema so a fresh host never starts with
    missing tables (the Windows install path has no sqlite3 CLI)."""
    try:
        import db_init

        db_init.init()
    except Exception as exc:  # never block server startup on this
        print(f"[mcp_server] schema self-heal skipped: {exc}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args, _ = parser.parse_known_args()
    if args.smoke:
        _smoke()
        sys.exit(0)
    _ensure_schema()
    prometheus_metrics.serve()
    mcp.run()
