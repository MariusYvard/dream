"""Four-role multi-agent debate and consensus mediation.

Roles share the same Ollama-served consolidation model with different system
prompts. The mediator aggregates scores into the final consensus.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
import model_profile
CONSOLIDATION_MODEL = model_profile.consolidation_model()

WEIGHTS = {
    "structural_coherence": 0.25,
    "logical_consistency": 0.30,
    "conciseness_score": 0.20,
    "domain_accuracy": 0.25,
}

ARCHIVISTE = (
    "You are the Archiviste. Capture facts verbatim. Anchor every claim in ISO 8601. "
    "Build a strict hierarchical summary (subject > verb > object > date). No paraphrase. "
    "Reject anything you cannot tie to a transcript span. "
    'Return strict JSON: {"summary": <str>, "structural_coherence": <0..1>}'
)
SCEPTIQUE = (
    "You are the Sceptique. Hunt contradictions, semantic drift, missing evidence. "
    "Compare each candidate fact against the existing graph neighbours (provided). "
    "Flag every internal inconsistency with a {fact_id, reason}. "
    'Return strict JSON: {"contradictions": [...], "logical_consistency": <0..1>}'
)
OPTIMISEUR = (
    "You are the Optimiseur. Compress the consolidated summary to the smallest form that "
    "preserves load-bearing content. Strip filler, examples, narrative. Hard target: <120 tokens per cluster. "
    'Return strict JSON: {"summary_compressed": <str>, "conciseness_score": <0..1>}'
)
EXPERT = (
    "You are the Expert Domaine. Validate technical accuracy and domain conventions against the topic files attached. "
    "Flag anything that contradicts established project context. "
    'Return strict JSON: {"domain_issues": [...], "domain_accuracy": <0..1>}'
)


@dataclass
class DebateResult:
    cluster_id: str
    summary: str
    score_final: float
    votes: dict[str, float]
    decision: str  # accept | hitl | reject
    trail: dict[str, Any]


def _ask(system: str, prompt: str) -> dict[str, Any]:
    payload = {
        "model": CONSOLIDATION_MODEL,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        return json.loads(resp.json()["response"])


def debate(cluster_id: str, cluster_text: str, neighbours_json: str) -> DebateResult:
    user_prompt = f"Cluster:\n{cluster_text}\n\nGraph neighbours:\n{neighbours_json}"

    arch = _ask(ARCHIVISTE, user_prompt)
    scep = _ask(SCEPTIQUE, user_prompt + f"\n\nArchiviste summary:\n{arch.get('summary','')}")
    opt = _ask(OPTIMISEUR, f"Summary to compress:\n{arch.get('summary','')}")
    exp = _ask(EXPERT, user_prompt + f"\n\nCompressed summary:\n{opt.get('summary_compressed','')}")

    votes = {
        "structural_coherence": float(arch.get("structural_coherence", 0.0)),
        "logical_consistency": float(scep.get("logical_consistency", 0.0)),
        "conciseness_score": float(opt.get("conciseness_score", 0.0)),
        "domain_accuracy": float(exp.get("domain_accuracy", 0.0)),
    }
    score_final = sum(votes[k] * WEIGHTS[k] for k in WEIGHTS)

    if score_final >= 0.7:
        decision = "accept"
    elif score_final >= 0.5:
        decision = "hitl"
    else:
        decision = "reject"

    return DebateResult(
        cluster_id=cluster_id,
        summary=opt.get("summary_compressed") or arch.get("summary", ""),
        score_final=score_final,
        votes=votes,
        decision=decision,
        trail={"archiviste": arch, "sceptique": scep, "optimiseur": opt, "expert": exp},
    )
