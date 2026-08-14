#!/usr/bin/env python3
"""Does the nightly consolidation actually improve the memory?

Seeds a day buffer with decisions where a newer one supersedes an older one,
plus noise, runs the real dream cycle, then probes the consolidated memory: for
each probe, is the top retrieved node closer to the CURRENT truth (Postgres)
than to the STALE one (MySQL)? Scoring is by embedding similarity, so it is
robust to the debate paraphrasing the summary.

Needs a local Ollama with the consolidation model (the cycle runs a multi-agent
debate). Skips cleanly if Ollama is down. Non-deterministic by nature: treat the
number as a signal, not a fixed score.

    python eval/consolidation_eval.py
    python eval/consolidation_eval.py --selftest   # deterministic, no stack
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "dream"
sys.path.insert(0, str(ROOT / "scripts"))

# (query, current truth, stale truth) — the top hit should sit closer to current.
PROBES = [
    ("which database does the project use", "the project database is PostgreSQL", "the project database is MySQL"),
]

# Day-buffer fixture. The second decision supersedes the first; the last line is
# noise the cycle should drop (not a decision, no load-bearing token).
BUFFER = [
    {"type": "decision", "content": "Decision: the project database is MySQL."},
    {"type": "decision", "content": "Decision: we migrated the database to PostgreSQL; MySQL is deprecated and removed."},
    {"type": "decision", "content": "Decision: the API rate limit is 100 requests per minute."},
    {"type": "decision", "content": "Decision: production is deployed in the eu-west-1 region."},
    {"type": "fact", "content": "thanks, that looks good to me"},
]


def correct(top_vec, current_vec, stale_vec) -> bool:
    """Top result is consolidated correctly if it is closer to the current truth
    than to the stale one. Vectors are normalised, so dot product is cosine."""
    import numpy as np

    t = np.asarray(top_vec, dtype=float)
    return float(t @ np.asarray(current_vec, dtype=float)) > float(t @ np.asarray(stale_vec, dtype=float))


def _selftest() -> int:
    assert correct([1.0, 0.0], [0.9, 0.1], [0.0, 1.0]) is True
    assert correct([0.0, 1.0], [0.9, 0.1], [0.0, 1.0]) is False
    print("selftest OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    home = Path(tempfile.mkdtemp(prefix="dream-conso-"))
    os.environ["DREAM_HOME"] = str(home)
    os.environ.setdefault("DREAM_METRICS_ENABLED", "0")

    from ollama_health import ollama_up
    if not ollama_up():
        print("Ollama not reachable; the cycle needs it. Skipping (this is fine).")
        return 0

    import db_init
    import lancedb_init
    import ledger_sign

    db_init.init()
    lancedb_init.init()
    ledger_sign.bootstrap()

    # Seed today's buffer directly (sanitised=llm skips the slow re-sanitise pass).
    bufdir = home / "buffer"
    bufdir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    with (bufdir / f"{today}.jsonl").open("w", encoding="utf-8") as fh:
        for ev in BUFFER:
            fh.write(json.dumps({**ev, "meta": {"sanitised": "llm"}}, ensure_ascii=False) + "\n")

    import scheduler
    print("running the consolidation cycle (multi-agent debate, may take a minute) ...")
    out = scheduler.run_cycle()
    print("cycle:", {k: out.get(k) for k in ("status", "raw_events", "load_bearing", "accepted", "rejected")})
    if out.get("status") != "ok" or not out.get("accepted"):
        print("nothing consolidated; cannot score. Try again with Ollama warm.")
        return 0

    import lancedb
    from mcp_search_activation import embedder

    table = lancedb.connect(str(home / "vectors.lance")).open_table("nodes")
    emb = embedder()

    hits = 0
    for query, current, stale in PROBES:
        qvec = emb.encode(query, normalize_embeddings=True).tolist()
        rows = table.search(qvec).limit(1).to_pandas()
        if rows.empty:
            print(f"  [{query}] no node retrieved")
            continue
        top = rows.iloc[0]["content"]
        top_vec = emb.encode(top, normalize_embeddings=True).tolist()
        cur_vec = emb.encode(current, normalize_embeddings=True).tolist()
        stale_vec = emb.encode(stale, normalize_embeddings=True).tolist()
        ok = correct(top_vec, cur_vec, stale_vec)
        hits += ok
        print(f"  [{query}] -> {'CURRENT' if ok else 'STALE'} | top: {top[:90]!r}")

    # Did the cycle drop the noise? (the chit-chat must not have become a node)
    all_content = " ".join(r["content"].lower() for _, r in table.search(emb.encode('x', normalize_embeddings=True).tolist()).limit(50).to_pandas().iterrows())
    noise_dropped = "looks good to me" not in all_content

    n = len(PROBES)
    print(f"\nconsolidation: supersession correct {hits}/{n} = {hits / n:.0%}; noise_dropped={noise_dropped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
