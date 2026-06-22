"""Vectorless, reasoning-based selection over the topic tree (PageIndex-style).

Instead of ranking topics by embedding cosine to the goal, a local LLM reads the
compact tree of summaries and picks the relevant node_ids with a one-line
rationale. Two payoffs: relevance beats raw similarity, and the embedder stays
off the load_context hot path (the cold bge-m3 load was the -32001 cause). The
rationale makes retrieval traceable.

Best-effort: returns None when the model is unreachable or the answer does not
parse, so load_context can fall back to the embedding path.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

import topic_tree

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = os.environ.get("DREAM_RETRIEVAL_MODEL", os.environ.get("DREAM_CONSOLIDATION_MODEL", "gemma4:12b"))

SYSTEM = (
    "You select the memory nodes relevant to the user's goal by reasoning over a "
    "table-of-contents tree, not by keyword overlap. Relevance, not similarity. "
    'Return strict JSON {"selected": [node_id, ...], "rationale": "<one short sentence>"}. '
    "Pick only what genuinely helps the goal, prefer leaves, an empty selection is valid."
)


def select(goal_text: str, tree: dict[str, Any] | None = None, *, max_chars: int = 4000) -> dict[str, Any] | None:
    tree = tree if tree is not None else topic_tree.load_tree()
    outline = topic_tree.render_for_llm(tree, max_chars=max_chars)
    if not outline.strip():
        return {"selected": [], "rationale": "memory is empty", "mode": "reasoning"}

    payload = {
        "model": MODEL,
        "system": SYSTEM,
        "prompt": f"Goal:\n{goal_text}\n\nMemory tree:\n{outline}",
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_predict": 256},
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            data = json.loads(resp.json().get("response", "{}"))
    except Exception:
        return None

    selected = data.get("selected")
    if not isinstance(selected, list):
        return None
    return {
        "selected": [str(s) for s in selected],
        "rationale": str(data.get("rationale", ""))[:300],
        "mode": "reasoning",
    }
