"""Counterfactual Garden (CER/DCER).

Generates 2-3 alternative branches from a seed node and scores them with a
critique. Accepted branches are written as read-only nodes under scenario=counterfactual.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
GEN_MODEL = os.environ.get("DREAM_COUNTERFACTUAL_MODEL", "gemma4:26b")

GENERATOR_PROMPT = (
    "You are the Counterfactual Generator. Given the seed event and the local context, "
    "produce 2 or 3 alternative actions that could have been taken at the same decision point. "
    "Each branch must specify action_alt, predicted_outcome, preconditions, horizon_days (1..30). "
    "Do not invent entities outside the provided context. "
    'Return strict JSON: {"branches": [{"action_alt": str, "predicted_outcome": str, "preconditions": [str], "horizon_days": int}]}'
)
CRITIQUE_PROMPT = (
    "You are the Critique. Score each branch on three axes 0..1: risk_score, coherence, alignment_with_goals. "
    'Return strict JSON: {"scores": [{"branch_index": int, "risk_score": float, "coherence": float, "alignment_with_goals": float}]}'
)


@dataclass
class Branch:
    action_alt: str
    predicted_outcome: str
    preconditions: list[str]
    horizon_days: int
    quality: float


def _ask(system: str, prompt: str) -> dict[str, Any]:
    payload = {
        "model": GEN_MODEL,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
    }
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        return json.loads(resp.json()["response"])


def generate_garden(seed: dict[str, Any], neighbours: list[dict[str, Any]]) -> list[Branch]:
    gen_input = json.dumps({"seed": seed, "neighbours": neighbours}, ensure_ascii=False)
    gen = _ask(GENERATOR_PROMPT, gen_input)
    raw_branches = gen.get("branches", [])
    if not raw_branches:
        return []

    crit_input = json.dumps({"branches": raw_branches}, ensure_ascii=False)
    crit = _ask(CRITIQUE_PROMPT, crit_input)
    scores = {s["branch_index"]: s for s in crit.get("scores", [])}

    output: list[Branch] = []
    for i, b in enumerate(raw_branches):
        s = scores.get(i, {"risk_score": 0.0, "coherence": 0.0, "alignment_with_goals": 0.0})
        quality = 0.4 * float(s["risk_score"]) + 0.3 * float(s["coherence"]) + 0.3 * float(s["alignment_with_goals"])
        if quality < 0.55:
            continue
        output.append(
            Branch(
                action_alt=b["action_alt"],
                predicted_outcome=b["predicted_outcome"],
                preconditions=b.get("preconditions", []),
                horizon_days=int(b.get("horizon_days", 7)),
                quality=quality,
            )
        )
    return output


def materialise(seed_id: str, branches: list[Branch]) -> list[dict[str, Any]]:
    """Return the node payloads to insert. Caller persists them under scenario=counterfactual."""
    now = dt.datetime.now(dt.timezone.utc)
    out: list[dict[str, Any]] = []
    for b in branches:
        nid = str(uuid.uuid4())
        out.append(
            {
                "id": nid,
                "type": "process",
                "content": f"If {', '.join(b.preconditions) or 'precondition'}: {b.action_alt}. Predicted: {b.predicted_outcome}.",
                "scenario": "counterfactual",
                "access_policy": "read_only",
                "validity": {
                    "from": now.isoformat(),
                    "to": (now + dt.timedelta(days=b.horizon_days)).isoformat(),
                    "confidence": b.quality,
                },
                "edge": {
                    "from": seed_id,
                    "to": nid,
                    "relation_type": "alternative_of",
                    "weight": b.quality,
                    "temporal_from": now.isoformat(),
                },
            }
        )
    return out
