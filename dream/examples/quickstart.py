#!/usr/bin/env python3
"""Dream quickstart — store memories, build the topic tree, and assemble a
session context, end to end, in a throwaway DREAM_HOME.

    python examples/quickstart.py

Requirements: the dream dependencies (pip install -r requirements.txt). The
embedding store (sentence-transformers + LanceDB) is required and the first run
loads the bge-m3 model once (about a minute). A local Ollama is OPTIONAL:
without it, load_context falls back from reasoning-based to embedding-based
retrieval. The full nightly consolidation debate does need a local LLM and is
shown at the end as a command, not run here.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# A throwaway home so the demo never touches a real install.
HOME = Path(tempfile.mkdtemp(prefix="dream-quickstart-"))
os.environ["DREAM_HOME"] = str(HOME)
os.environ.setdefault("DREAM_METRICS_ENABLED", "0")  # no metrics port for a demo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def banner(step: str) -> None:
    print(f"\n=== {step} ===")


def main() -> int:
    print(f"DREAM_HOME = {HOME}  (throwaway, safe to delete)")

    banner("1. initialise the stores")
    import db_init
    import lancedb_init
    import ledger_sign

    db_init.init()              # SQLite schema (nodes, edges, ledger, ...)
    lancedb_init.init()         # LanceDB vector table
    ledger_sign.bootstrap()     # Ed25519 keys for the signed ledger
    print("SQLite schema, LanceDB table and Ed25519 ledger ready.")

    banner("2. write a few memories into the PGT (vector + SQLite + ledger + graph)")
    from node_store import persist_node

    memories = [
        ("decision", "We standardise on snake_case across the codebase."),
        ("decision", "Postgres replaces SQLite for the analytics store."),
        ("fact", "The release train ships every second Thursday."),
    ]
    for node_type, content in memories:
        r = persist_node(content=content, node_type=node_type)
        print(f"  + {node_type:<8} {r['id'][:8]}  ledger={r['ledger']}")
    print(f"ledger verifies: {ledger_sign.verify()}")

    banner("3. topic tree — a PageIndex-style table of contents over memory")
    import topic_tree

    # Seed one consolidated topic line so the tree has a branch to show without
    # running the full nightly debate (which needs a local 12B model).
    topics_dir = HOME / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    (topics_dir / "decision.md").write_text(
        "\n- 2026-01-01T00:00:00+00:00 :: Naming is snake_case; analytics store is Postgres.\n",
        encoding="utf-8",
    )
    tree = topic_tree.build_and_save()
    print(topic_tree.render_for_llm(tree) or "  (empty)")

    banner("4. assemble a session context (load_context)")
    from load_context import build_bundle

    bundle = build_bundle("remind me of our naming and storage decisions", token_budget=1500)
    print(f"  retrieval_mode   : {bundle.get('retrieval_mode')}  "
          f"(reasoning needs Ollama, else embedding fallback)")
    if bundle.get("retrieval_rationale"):
        print(f"  rationale        : {bundle['retrieval_rationale']}")
    print(f"  topics loaded    : {len(bundle.get('topics', []))}")
    print(f"  recent decisions : {len(bundle.get('recent_decisions', []))}")
    print(f"  total tokens     : {bundle.get('total_tokens')}")

    banner("next: the nightly consolidation")
    print("  A real install captures session transcripts into the day buffer and")
    print("  consolidates them at 02:05 with a local multi-agent LLM debate:")
    print("    python scripts/scheduler.py --once")
    print(f"\nDone. Throwaway home at {HOME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
