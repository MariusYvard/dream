"""Nightly scheduler. Fires the dream consolidation cycle at 02:05 local time.

One-shot:
    python scheduler.py --once

Daemon:
    python scheduler.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import circuit_breaker
import dream_buffer
import ledger_sign
import llm
import prometheus_metrics
from consensus_router import debate
from dream_buffer import iter_buffer
from mcp_search_activation import embedder
from ollama_health import ollama_up
from vitality_engine import VitalityInputs, compute as vitality_compute, tier_for

import load_bearing
import node_store
import observability
import session_scan
from sanitize_local import sanitize as _sanitize_full

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
DB_PATH = DREAM_HOME / "pgt.sqlite"
TOPICS_DIR = DREAM_HOME / "topics"
ARCHIVE_DIR = DREAM_HOME / "archive" / "cold"
REJECTED_DIR = DREAM_HOME / "rejected"
CLAUDE_MD = DREAM_HOME / "CLAUDE.md"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
# httpx logs one INFO line per request: hundreds of them per cycle, drowning the
# handful of lines that say what the cycle actually did. This is the log nobody
# is awake to read at 02:05, so it has to stay legible after the fact.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("dream.scheduler")

# Evidence fed to one debate, in characters.
_MAX_CLUSTER_CHARS = int(os.environ.get("DREAM_MAX_CLUSTER_CHARS", "12000"))


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _postpone_if_machine_is_busy() -> bool:
    """Give the machine back to whoever is using it.

    The nightly task now carries StartWhenAvailable, so a night the PC was off
    means the cycle fires the moment it comes back, which is the middle of a
    working day. The cycle then wants the embedder (2.3 GB) and a CLI process
    per debate, alongside Claude Desktop and an IDE. Nothing here is urgent:
    the days stay pending and the next run picks them up.
    """
    floor_mb = float(os.environ.get("DREAM_MIN_FREE_MB", "2500"))
    try:
        import psutil

        free_mb = psutil.virtual_memory().available / (1024 * 1024)
    except Exception:
        return False  # cannot measure, do not block on a guess
    if free_mb < floor_mb:
        log.warning("only %.0f MB free (floor %.0f), postponing the cycle", free_mb, floor_mb)
        return True
    return False


def _refuse_if_unsafe() -> bool:
    if not ledger_sign.verify():
        log.error("ledger merkle check failed, refusing cycle")
        prometheus_metrics.CYCLE_FAILED.labels(phase="precheck").inc()
        return True
    state = circuit_breaker._load()
    if state.mode == "SECURISE":
        log.warning("circuit breaker in SECURISE mode, skipping cycle")
        return True
    return False


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union)


def _tokenize(text: str) -> set[str]:
    """Minimal tokenizer: lowercase words >= 3 chars, no stopwords."""
    STOP = {
        "les", "des", "une", "que", "qui", "est", "pas", "par", "sur",
        "the", "and", "for", "not", "but", "with", "this", "that",
    }
    words = set(text.lower().split())
    return {w for w in words if len(w) >= 3 and w not in STOP}


def _cluster_events(events: list[dict[str, Any]], sim_threshold: float = 0.25) -> list[dict[str, Any]]:
    """Two-pass clustering: first by type, then merge within each type by lexical overlap.

    Two events are in the same cluster when their Jaccard word-overlap exceeds
    `sim_threshold`. The threshold is intentionally low (0.25) so related facts
    from the same conversation end up in the same cluster without over-splitting.

    Falls back gracefully to one-per-type grouping when the event volume is low
    (< 5 per type), since Jaccard is noisy on very short texts.
    """
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        by_type[ev.get("type", "fact")].append(ev)

    clusters: list[dict[str, Any]] = []
    for ev_type, items in by_type.items():
        if len(items) < 5:
            # Small bucket — keep as a single cluster, no Jaccard needed.
            clusters.append({"cluster_id": str(uuid.uuid4()), "type": ev_type, "events": items})
            continue

        # Greedy single-linkage Jaccard clustering.
        token_sets = [_tokenize(ev.get("content", "")) for ev in items]
        assigned: list[int | None] = [None] * len(items)
        cluster_buckets: list[list[int]] = []

        for i in range(len(items)):
            best_cluster = -1
            best_score = 0.0
            for ci, bucket in enumerate(cluster_buckets):
                # Compare against the first member (representative) of the cluster.
                score = _jaccard(token_sets[i], token_sets[bucket[0]])
                if score > best_score:
                    best_score = score
                    best_cluster = ci
            if best_cluster >= 0 and best_score >= sim_threshold:
                cluster_buckets[best_cluster].append(i)
                assigned[i] = best_cluster
            else:
                assigned[i] = len(cluster_buckets)
                cluster_buckets.append([i])

        for bucket in cluster_buckets:
            clusters.append({
                "cluster_id": str(uuid.uuid4()),
                "type": ev_type,
                "events": [items[i] for i in bucket],
            })

    return clusters


def _load_bearing(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("type") in {"decision", "error"}:
            keep.append(ev); continue
        try:
            if load_bearing.classify(ev.get("content", "")):
                keep.append(ev)
        except Exception:
            # never lose the cycle to a classifier hiccup: degrade to lexical
            if load_bearing.lexical_hit(ev.get("content", "")):
                keep.append(ev)
    return keep


def _upgrade_sanitisation(events: list[dict[str, Any]]) -> int:
    """Run the full LLM redaction on events the Stop hook only regex-redacted,
    so personal data is scrubbed before it reaches topics or the graph. The hook
    keeps the fast path; the heavy pass happens here, off session-exit latency."""
    upgraded = 0
    for ev in events:
        if (ev.get("meta") or {}).get("sanitised") == "regex":
            try:
                ev["content"] = _sanitize_full(ev.get("content", "")).text
                ev.setdefault("meta", {})["sanitised"] = "llm"
                upgraded += 1
            except Exception:
                pass
    return upgraded


def _graph_neighbours(cluster_type: str, limit: int = 12) -> str:
    """Return a JSON list of existing high-vitality base nodes of the same type.

    Gives the Sceptique and Expert roles real graph context to compare each
    candidate fact against, instead of the empty list they used to receive.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT id, type, content FROM nodes "
                "WHERE scenario = 'base' AND status = 'active' AND type = ? "
                "ORDER BY vitality DESC LIMIT ?",
                (cluster_type, limit),
            ).fetchall()
    except sqlite3.Error as exc:
        log.warning("neighbour lookup failed for type %s: %s", cluster_type, exc)
        return "[]"
    return json.dumps(
        [{"id": r[0], "type": r[1], "content": r[2][:240]} for r in rows],
        ensure_ascii=False,
    )


def _goals_vector(conn: sqlite3.Connection):
    """Mean embedding of the most recent high-vitality decisions, or None."""
    import numpy as np

    rows = conn.execute(
        "SELECT content FROM nodes WHERE type = 'decision' AND vitality > 0.7 "
        "ORDER BY updated_at DESC LIMIT 5"
    ).fetchall()
    if not rows:
        return None
    embs = embedder().encode([r[0] for r in rows], normalize_embeddings=True)
    return np.mean(embs, axis=0)


def _contradiction_weight(conn: sqlite3.Connection, node_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(weight), 0) FROM edges WHERE to_id = ? AND relation_type = 'contradicts'",
        (node_id,),
    ).fetchone()
    return float(row[0] or 0.0)


def _recompute_vitality() -> int:
    """Recompute vitality for every active base node so temporal decay fires
    during the nightly cycle.

    Without this pass the vitality column only moves when update_vitality is
    called on access, so an old unused node keeps its original vitality forever
    and never cools down to the cold tier. This is the pass that makes automatic
    forgetting actually happen.
    """
    import numpy as np

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, content, access_count, last_accessed FROM nodes "
            "WHERE scenario = 'base' AND status = 'active'"
        ).fetchall()
        if not rows:
            return 0
        goals_vec = _goals_vector(conn)
        embs = embedder().encode([r["content"] for r in rows], normalize_embeddings=True)
        now = _now()
        updated = 0
        for r, emb in zip(rows, embs):
            vi = VitalityInputs(
                last_accessed=dt.datetime.fromisoformat(r["last_accessed"]) if r["last_accessed"] else None,
                access_count=int(r["access_count"] or 0),
                co_activation_score=0.0,
                node_embedding=np.asarray(emb, dtype=float),
                goals_embedding=goals_vec,
                contradiction_weight=_contradiction_weight(conn, r["id"]),
            )
            conn.execute(
                "UPDATE nodes SET vitality = ?, updated_at = ? WHERE id = ?",
                (vitality_compute(vi), now, r["id"]),
            )
            updated += 1
        conn.commit()
    return updated


def _verify_expired_branches() -> int:
    """Verify counterfactual branches whose horizon has elapsed: promote, decay
    or prune by comparing the predicted outcome against the current graph."""
    from mcp_search_activation import hybrid_search

    now_iso = _now()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        branches = conn.execute(
            "SELECT id, content, vitality FROM nodes "
            "WHERE scenario = 'counterfactual' AND status = 'active' "
            "AND validity_to != '' AND validity_to <= ?",
            (now_iso,),
        ).fetchall()

    verified = 0
    for b in branches:
        try:
            hits = hybrid_search(query=b["content"], k=10, vitality_min=0.4, rerank=False)
        except Exception as exc:
            log.warning("counterfactual verify failed on %s: %s", b["id"], exc)
            continue
        match = max((h.cosine for h in hits), default=0.0)
        with sqlite3.connect(DB_PATH) as conn:
            if match >= 0.75:
                conn.execute(
                    "UPDATE nodes SET scenario = 'base', type = 'process', access_policy = 'read_write' WHERE id = ?",
                    (b["id"],),
                )
            elif match >= 0.5:
                conn.execute(
                    "UPDATE nodes SET confidence = ? WHERE id = ?",
                    (float(b["vitality"]) * 0.7, b["id"]),
                )
            else:
                conn.execute("UPDATE nodes SET status = 'archived' WHERE id = ?", (b["id"],))
            conn.commit()
        verified += 1
    return verified


def _counterfactual_pass(max_seeds: int = 3) -> dict[str, int]:
    """Autonomously grow and verify the Counterfactual Garden.

    Seeds branches on recent error nodes that have none yet, then verifies
    branches whose horizon has elapsed. Previously both ran only on manual skill
    invocation, so the garden was inert under autonomous operation.
    """
    import counterfactual_garden

    now = _now()
    seeded = 0
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        seeds = [
            dict(r)
            for r in conn.execute(
                "SELECT id, content, type FROM nodes "
                "WHERE scenario = 'base' AND status = 'active' AND type = 'error' "
                "AND id NOT IN (SELECT from_id FROM edges WHERE relation_type = 'alternative_of') "
                "ORDER BY created_at DESC LIMIT ?",
                (max_seeds,),
            ).fetchall()
        ]

    for seed in seeds:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            neighbours = [
                dict(r)
                for r in conn.execute(
                    "SELECT n.id, n.content, n.type FROM edges e JOIN nodes n ON n.id = e.to_id "
                    "WHERE e.from_id = ? LIMIT 12",
                    (seed["id"],),
                ).fetchall()
            ]
        try:
            branches = counterfactual_garden.generate_garden(seed, neighbours)
            payloads = counterfactual_garden.materialise(seed["id"], branches)
        except Exception as exc:
            log.warning("counterfactual seeding failed on %s: %s", seed["id"], exc)
            prometheus_metrics.CYCLE_FAILED.labels(phase="counterfactual").inc()
            continue
        with sqlite3.connect(DB_PATH) as conn:
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
                seeded += 1
            conn.commit()

    verified = _verify_expired_branches()
    return {"seeded": seeded, "verified": verified}


def _write_topic(cluster_type: str, summary: str) -> None:
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOPICS_DIR / f"{cluster_type}.md"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n- {_now()} :: {summary}\n")


def _rebuild_claude_md() -> int:
    # Prefer the hierarchical tree index (a table of contents); the tree json is
    # also what load_context reasons over. Fall back to the flat node list.
    try:
        import topic_tree

        tree = topic_tree.build_and_save()
        if tree.get("children"):
            text = topic_tree.render_index_md(tree)
            CLAUDE_MD.parent.mkdir(parents=True, exist_ok=True)
            CLAUDE_MD.write_text(text, encoding="utf-8")
            return len(text) // 4
    except Exception as exc:
        log.warning("tree-based CLAUDE.md skipped: %s", exc)

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT type, content FROM nodes WHERE vitality > 0.5 AND scenario = 'base' ORDER BY vitality DESC LIMIT 30"
        ).fetchall()
    lines = ["# Dream Index", _now(), ""]
    for tp, content in rows:
        lines.append(f"- [{tp}] {content[:120]}")
    text = "\n".join(lines)
    if len(text) // 4 > 500:
        text = text[: 500 * 4]
    CLAUDE_MD.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_MD.write_text(text, encoding="utf-8")
    return len(text) // 4


def _publish_health_metrics(accepted: int, hitl: int, rejected: int) -> None:
    """Write the circuit-breaker inputs and Prometheus gauges after a cycle.

    Feeds the three cache keys health_check reads (consensus_rate_24h,
    ram_peak_mb) plus the gauges that were previously declared but never set.
    """
    import cache_layer

    decided = accepted + hitl + rejected
    consensus_rate = (accepted / decided) if decided else 1.0
    cache_layer.set("metric:consensus_rate_24h", consensus_rate, ttl=86400)

    ram_mb = 0.0
    try:
        import psutil

        ram_mb = psutil.Process().memory_info().rss / (1024 * 1024)
        prev = float(cache_layer.get("metric:ram_peak_mb") or 0.0)
        ram_mb = max(prev, ram_mb)
        cache_layer.set("metric:ram_peak_mb", ram_mb, ttl=86400)
        prometheus_metrics.RAM_PEAK.set(ram_mb)
    except Exception as exc:  # psutil missing or platform quirk
        log.warning("RAM measurement skipped: %s", exc)

    try:
        with sqlite3.connect(DB_PATH) as conn:
            vit = conn.execute(
                "SELECT AVG(vitality) FROM nodes WHERE status = 'active'"
            ).fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM hitl_queue WHERE resolved_at IS NULL"
            ).fetchone()[0]
        prometheus_metrics.VITALITY_AVG.set(float(vit or 0.0))
        prometheus_metrics.HITL_PENDING.set(int(pending or 0))
        prometheus_metrics.LEDGER_OK.set(1 if ledger_sign.verify() else 0)
    except Exception as exc:
        log.warning("gauge publish skipped: %s", exc)


def _run_cycle_inner() -> dict[str, Any]:
    cycle_id = str(uuid.uuid4())
    started = _now()
    log.info("cycle %s starting", cycle_id)
    if _refuse_if_unsafe():
        return {"status": "skipped"}
    if _postpone_if_machine_is_busy():
        return {"status": "postponed_low_memory", "cycle_id": cycle_id, "started_at": started}

    metrics: dict[str, Any] = {"cycle_id": cycle_id, "started_at": started}

    # The debate needs *a* provider, not Ollama specifically: since llm.py the
    # reasoning roles default to the Claude CLI. Probe what the consolidation
    # role can actually reach and refuse loudly only when nothing answers,
    # instead of failing on a daemon the cycle may no longer use.
    prometheus_metrics.OLLAMA_UP.set(1 if ollama_up() else 0)
    reachable = [p for p in ("claude", "ollama") if llm.available(p)]
    if not reachable:
        log.error("no LLM provider reachable (claude CLI absent, ollama down), skipping")
        prometheus_metrics.CYCLE_FAILED.labels(phase="provider").inc()
        return {"status": "skipped_no_provider", **metrics}
    metrics["providers"] = reachable
    metrics["provider_used"] = llm.provider_for("consolidation")

    # Ingest before consolidating: the Stop hook is not a reliable feed (it does
    # not fire under Cowork), so read the transcripts off disk first.
    try:
        metrics["scan"] = session_scan.scan()
        log.info("session scan: %s", metrics["scan"])
    except Exception as exc:
        log.warning("session scan skipped: %s", exc)
        metrics["scan"] = {"error": str(exc)}

    # Catch-up: every day still unconsolidated, oldest first, not just today.
    days = dream_buffer.pending_days()
    metrics["days"] = [d.isoformat() for d in days]
    raw = [ev for day in days for ev in iter_buffer(day)]
    metrics["raw_events"] = len(raw)

    # Filter first, sanitise second. The LLM sanitisation pass is a serial
    # ~1 s local call per event; running it on the raw buffer meant paying it on
    # the ~90% of events that the load-bearing filter drops one line later. With
    # a hook-fed buffer of twenty events nobody noticed; with a transcript scan
    # feeding hundreds, it stalled the whole cycle. The guarantee is unchanged:
    # nothing reaches topics or the graph without the full pass.
    lb_events = _load_bearing(raw)
    metrics["load_bearing"] = len(lb_events)
    if len(lb_events) < 3:
        log.info("buffer too sparse (%d), skipping cycle", len(lb_events))
        return {"status": "skipped_sparse", **metrics}
    metrics["sanitised_upgraded"] = _upgrade_sanitisation(lb_events)

    accepted = hitl = rejected = 0
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)

    # A debate is four provider calls, ~35 s. Unbounded, the first scan of a
    # months-old transcript backlog produced 778 clusters: seven hours. Rank by
    # cluster size (a point made once is noise, a point made in six places is a
    # pattern) and spend the budget on the top of the list. The tail is dropped
    # on purpose: a memory that keeps everything is a log, not a memory.
    ranked = sorted(_cluster_events(lb_events), key=lambda c: len(c["events"]), reverse=True)
    budget = int(os.environ.get("DREAM_MAX_CLUSTERS", "25"))
    metrics["clusters_total"] = len(ranked)
    metrics["clusters_skipped"] = max(0, len(ranked) - budget)
    if metrics["clusters_skipped"]:
        log.info("%d clusters, debating the top %d by size", len(ranked), budget)

    for cluster in ranked[:budget]:
        # Cap the debate input. The largest cluster held 116 events, ~460 kB,
        # and the CLI exited 1 on it. A consolidation summary does not get
        # better past a few pages of evidence, it gets slower and more fragile.
        text = "\n".join(ev["content"] for ev in cluster["events"])[:_MAX_CLUSTER_CHARS]
        neighbours_json = _graph_neighbours(cluster["type"])
        try:
            result = debate(cluster["cluster_id"], text, neighbours_json)
        except Exception as exc:
            log.exception("debate failed on cluster %s: %s", cluster["cluster_id"], exc)
            prometheus_metrics.CYCLE_FAILED.labels(phase="debate").inc()
            continue
        prometheus_metrics.CONSENSUS_SCORE.observe(result.score_final)
        if result.decision == "accept":
            _write_topic(cluster["type"], result.summary)
            ledger_sign.append_leaf("consolidate_accept", None, {"cluster_id": cluster["cluster_id"], "score": result.score_final})
            # Also materialise the consolidated fact as a graph node so
            # search_semantic, query_relations and the CLAUDE.md index reflect
            # consolidation, not just store_event. Best-effort: a node write
            # must not abort the cycle.
            try:
                _projs = {(ev.get("meta") or {}).get("project") for ev in cluster["events"]}
                _proj = _projs.pop() if len(_projs) == 1 else None
                node_store.persist_node(
                    content=result.summary,
                    node_type=cluster["type"],
                    vitality=0.9,
                    confidence=result.score_final,
                    source_session="nightly_consolidation",
                    project=_proj,
                    ledger_op="consolidate_node",
                )
            except Exception as exc:
                log.warning("node persist failed for cluster %s: %s", cluster["cluster_id"], exc)
                prometheus_metrics.CYCLE_FAILED.labels(phase="persist_node").inc()
            accepted += 1
        elif result.decision == "hitl":
            trail_path = REJECTED_DIR / f"{cluster['cluster_id']}_hitl.json"
            trail_path.write_text(json.dumps(result.trail, ensure_ascii=False), encoding="utf-8")
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO hitl_queue (node_id, debate_trail_path, score_final, created_at) VALUES (?, ?, ?, ?)",
                    (cluster["cluster_id"], str(trail_path), result.score_final, _now()),
                )
                conn.commit()
            hitl += 1
        else:
            trail_path = REJECTED_DIR / f"{cluster['cluster_id']}_rejected.json"
            trail_path.write_text(json.dumps(result.trail, ensure_ascii=False), encoding="utf-8")
            rejected += 1

    # Checkpoint here, not at the end. Everything above is the expensive,
    # irreversible part: the debates are done, the topics are written, the
    # ledger has its leaves. Everything below (vitality, tiering, the
    # counterfactual garden) is bookkeeping that loads a 2.3 GB embedder and has
    # already been killed mid-flight by memory pressure on this machine. When
    # that happened the days were never marked, so the next cycle re-debated the
    # same 25 clusters from scratch. Consolidated work stays consolidated.
    for day in days:
        dream_buffer.mark_consolidated(day)
    log.info("checkpoint: %d accepted, %d hitl, %d rejected, %d days marked",
             accepted, hitl, rejected, len(days))

    # Recompute vitality first so the tier decision below reads fresh, decayed
    # values instead of whatever was last written on access.
    try:
        metrics["vitality_recomputed"] = _recompute_vitality()
    except Exception as exc:
        log.warning("vitality recompute skipped: %s", exc)
        prometheus_metrics.CYCLE_FAILED.labels(phase="vitality").inc()
        metrics["vitality_recomputed"] = 0

    promotions = demotions = 0
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        for row in conn.execute("SELECT id, vitality, content FROM nodes WHERE scenario = 'base' AND status = 'active'").fetchall():
            v = float(row[1] or 0.0)
            tier = tier_for(v)
            if tier == "cold":
                (ARCHIVE_DIR / f"{dt.date.today().isoformat()}.jsonl").open("a", encoding="utf-8").write(
                    json.dumps({"id": row[0], "content": row[2], "vitality": v}) + "\n"
                )
                conn.execute("UPDATE nodes SET status = 'archived' WHERE id = ?", (row[0],))
                demotions += 1
            elif tier == "hot":
                promotions += 1
        conn.commit()

    # Grow and verify the Counterfactual Garden on error nodes and expired
    # branches. Best-effort: a failure here must not abort the cycle.
    try:
        cf = _counterfactual_pass()
    except Exception as exc:
        log.warning("counterfactual pass skipped: %s", exc)
        prometheus_metrics.CYCLE_FAILED.labels(phase="counterfactual").inc()
        cf = {"seeded": 0, "verified": 0}

    tokens = _rebuild_claude_md()
    metrics.update({
        "accepted": accepted, "hitl": hitl, "rejected": rejected,
        "promotions": promotions, "demotions": demotions, "claude_md_tokens": tokens,
        "cf_seeded": cf["seeded"], "cf_verified": cf["verified"],
        "finished_at": _now(),
    })

    _publish_health_metrics(accepted, hitl, rejected)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO cycle_state (cycle_id, started_at, finished_at, phase, metrics_json) VALUES (?, ?, ?, ?, ?)",
            (cycle_id, started, metrics["finished_at"], "done", json.dumps(metrics)),
        )
        conn.commit()
    prometheus_metrics.CYCLE_COMPLETED.inc()
    log.info("cycle %s done: %s", cycle_id, metrics)
    return {"status": "ok", **metrics}


def run_cycle() -> dict[str, Any]:
    """Run one cycle and record its outcome (every path, including the skips)
    so health_check exposes last_cycle_status / last_cycle_at."""
    out = _run_cycle_inner()
    try:
        observability.record_cycle(out.get("status", "unknown"), out)
    except Exception:
        pass
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cron", default="5 2 * * *", help="Cron expression, default 02:05 daily")
    args = parser.parse_args()

    # The one caller allowed to spawn `claude -p`: this runs from Task
    # Scheduler, outside any Claude session, with a three-hour budget. See
    # llm.cli_allowed for why everything else is forbidden.
    os.environ.setdefault("DREAM_ALLOW_CLI", "1")

    # Self-repair before anything else: an unattended 02:05 run has nobody to
    # read a diagnostic, so doctor fixes what it can instead of only reporting.
    try:
        import doctor

        for line in doctor.repair():
            log.info("repair: %s", line)
    except Exception as exc:
        log.warning("self-repair skipped: %s", exc)

    prometheus_metrics.serve()

    if args.once:
        out = run_cycle()
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(0)

    sched = BlockingScheduler(timezone=dt.datetime.now().astimezone().tzinfo)
    minute, hour, dom, mon, dow = args.cron.split()
    sched.add_job(run_cycle, CronTrigger(minute=minute, hour=hour, day=dom, month=mon, day_of_week=dow))
    log.info("scheduler armed: %s", args.cron)
    sched.start()


if __name__ == "__main__":
    main()
