"""Token-efficient session context assembler.

Picks the top-vitality nodes whose embedding aligns with the active goal,
respects a token budget, drops the lowest-vitality topic first when over budget.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

import cache_layer
from mcp_search_activation import embedder, graph

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
DB_PATH = DREAM_HOME / "pgt.sqlite"
CLAUDE_MD = DREAM_HOME / "CLAUDE.md"
TOPICS_DIR = DREAM_HOME / "topics"


def _approx_tokens(text: str) -> int:
    return max(1, int(len(text) / 4))


def _load_topics(exclude: list[str] | None) -> list[dict[str, Any]]:
    if not TOPICS_DIR.exists():
        return []
    topics: list[dict[str, Any]] = []
    ex = set(exclude or [])
    for path in TOPICS_DIR.glob("*.md"):
        name = path.stem
        if name in ex:
            continue
        content = path.read_text(encoding="utf-8")
        topics.append({"name": name, "content": content, "tokens": _approx_tokens(content)})
    return topics


_EMB_CACHE_PATH = TOPICS_DIR / ".embcache.json"


def _topic_embeddings(topics: list[dict[str, Any]]) -> np.ndarray:
    """Embed topics, reusing a per-content-hash cache so unchanged topics are
    never re-encoded. Re-encoding every topic on every call was the cold-path
    cost behind the load_context -32001 timeouts."""
    import hashlib

    cache: dict[str, list[float]] = {}
    try:
        if _EMB_CACHE_PATH.exists():
            cache = json.loads(_EMB_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        cache = {}

    shas = [hashlib.sha256(t["content"].encode("utf-8")).hexdigest() for t in topics]
    missing = [t["content"] for t, s in zip(topics, shas) if s not in cache]
    if missing:
        fresh = embedder().encode(missing, normalize_embeddings=True)
        fresh_list = fresh.tolist() if hasattr(fresh, "tolist") else list(fresh)
        it = iter(fresh_list)
        for s in shas:
            if s not in cache:
                cache[s] = next(it)
        try:
            present = set(shas)
            cache = {k: v for k, v in cache.items() if k in present}  # prune removed topics
            _EMB_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _EMB_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
        except Exception:
            pass
    return np.asarray([cache[s] for s in shas], dtype=float)


def _rank_topics(topics: list[dict[str, Any]], goal_vec: np.ndarray) -> list[dict[str, Any]]:
    if not topics:
        return []
    emb = _topic_embeddings(topics)
    scores = emb @ goal_vec
    for t, s in zip(topics, scores.tolist()):
        t["score"] = float(s)
    return sorted(topics, key=lambda x: x["score"], reverse=True)


def _trim_to_budget(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    used = 0
    for it in items:
        if used + it["tokens"] > budget:
            continue
        out.append(it)
        used += it["tokens"]
    return out


def build_bundle(goal_text: str, token_budget: int = 2000, vitality_min: float = 0.5, topics_exclude: list[str] | None = None) -> dict[str, Any]:
    if token_budget < 256:
        raise ValueError("token_budget too small")

    cache_key = f"ctx:{hash((goal_text, token_budget, vitality_min))}"
    cached = cache_layer.get(cache_key)
    if cached is not None:
        return cached

    goal_vec = embedder().encode(goal_text, normalize_embeddings=True)

    topics = _rank_topics(_load_topics(topics_exclude), goal_vec)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, content FROM nodes WHERE type='decision' AND vitality >= ? AND scenario='base' "
            "ORDER BY updated_at DESC LIMIT 8",
            (vitality_min,),
        )
        recent_decisions = [{"id": r[0], "summary": r[1][:240]} for r in cur.fetchall()]
        cur.execute(
            "SELECT n.id, n.content FROM nodes n JOIN hitl_queue h ON h.node_id = n.id "
            "WHERE h.resolved_at IS NULL ORDER BY h.created_at ASC LIMIT 5"
        )
        pending_hitl = [{"id": r[0], "summary": r[1][:240]} for r in cur.fetchall()]

    claude_md = CLAUDE_MD.read_text(encoding="utf-8") if CLAUDE_MD.exists() else ""
    claude_md_tokens = _approx_tokens(claude_md)

    remaining = max(0, token_budget - claude_md_tokens)
    topics_trimmed = _trim_to_budget(topics, remaining)
    total = claude_md_tokens + sum(t["tokens"] for t in topics_trimmed)

    bundle = {
        "claude_md": claude_md,
        "topics": topics_trimmed,
        "recent_decisions": recent_decisions,
        "pending_hitl": pending_hitl,
        "total_tokens": total,
    }
    cache_layer.set(cache_key, bundle, ttl=300)
    return bundle
