#!/usr/bin/env python3
"""Reproducible retrieval-recall eval for the Dream PGT.

Stores a synthetic fact set, then queries each fact by a paraphrase and measures
recall@k: does the right memory come back in the top k? It exercises the real
write + embedding + vector-search path, runs without any LLM server, and prints
a number you can track across changes.

    python eval/recall_eval.py
    python eval/recall_eval.py --k 1 3 5 --dataset eval/dataset.jsonl

The set is synthetic and small (distinct facts with near-neighbour distractors),
so this is a sanity / regression signal, not a leaderboard claim. Extend
eval/dataset.jsonl with your own domain to make it meaningful for your use case.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _load_dataset(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(Path(__file__).resolve().parent / "dataset.jsonl"))
    ap.add_argument("--k", nargs="+", type=int, default=[1, 3, 5])
    args = ap.parse_args()

    home = Path(tempfile.mkdtemp(prefix="dream-eval-"))
    os.environ["DREAM_HOME"] = str(home)
    os.environ.setdefault("DREAM_METRICS_ENABLED", "0")

    data = _load_dataset(Path(args.dataset))
    print(f"dataset: {len(data)} fact/query pairs")
    print(f"profile: {os.environ.get('DREAM_PROFILE', 'full')}")

    import db_init
    import lancedb_init
    import ledger_sign

    db_init.init()
    lancedb_init.init()
    ledger_sign.bootstrap()

    import lancedb

    from mcp_search_activation import embedder
    from node_store import persist_node

    print("storing facts ...")
    ids = [persist_node(content=row["fact"], node_type="fact")["id"] for row in data]

    table = lancedb.connect(str(home / "vectors.lance")).open_table("nodes")
    emb = embedder()
    maxk = max(args.k)

    hits = {k: 0 for k in args.k}
    for target, row in zip(ids, data):
        qvec = emb.encode(row["query"], normalize_embeddings=True).tolist()
        got = table.search(qvec).limit(maxk).to_pandas()["id"].tolist()
        for k in args.k:
            if target in got[:k]:
                hits[k] += 1

    n = len(data)
    print("\nretrieval recall (embedding search, no rerank, no LLM):")
    for k in sorted(args.k):
        print(f"  recall@{k}: {hits[k]}/{n} = {hits[k] / n:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
