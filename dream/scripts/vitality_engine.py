"""Vitality update engine.

Implements V(t+1) = alpha * exp(-lambda * dt) + beta * H(usage) + gamma * R(goal) - delta * C
with calibrated defaults from references/vitality-formula.md.
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

ALPHA = 0.30
BETA = 0.35
GAMMA = 0.30
DELTA = 0.10
LAMBDA = 0.05  # per day

THRESHOLDS = {
    "hot": 0.85,
    "active": 0.40,
    "dim": 0.20,
}


@dataclass
class VitalityInputs:
    last_accessed: dt.datetime | None
    access_count: int
    co_activation_score: float
    node_embedding: np.ndarray
    goals_embedding: np.ndarray | None
    contradiction_weight: float


def _hebbian(access_count: int, co_activation: float) -> float:
    return math.log(1.0 + access_count + co_activation)


def _goal_alignment(node: np.ndarray, goals: np.ndarray | None) -> float:
    if goals is None or np.linalg.norm(node) == 0 or np.linalg.norm(goals) == 0:
        return 0.0
    cosine = float(np.dot(node, goals) / (np.linalg.norm(node) * np.linalg.norm(goals)))
    return max(0.0, cosine)


def compute(vi: VitalityInputs, now: dt.datetime | None = None) -> float:
    now = now or dt.datetime.now(dt.timezone.utc)
    if vi.last_accessed is None:
        dt_days = 0.0
    else:
        dt_days = max(0.0, (now - vi.last_accessed).total_seconds() / 86400.0)

    decay = ALPHA * math.exp(-LAMBDA * dt_days)
    hebb = BETA * _hebbian(vi.access_count, vi.co_activation_score)
    goal = GAMMA * _goal_alignment(vi.node_embedding, vi.goals_embedding)
    penalty = DELTA * vi.contradiction_weight
    v = decay + hebb + goal - penalty
    return max(0.0, min(1.0, v))


def tier_for(v: float) -> str:
    if v > THRESHOLDS["hot"]:
        return "hot"
    if v >= THRESHOLDS["active"]:
        return "active"
    if v >= THRESHOLDS["dim"]:
        return "dim"
    return "cold"


def batch_update(db_path: Path, node_ids: Iterable[str], inputs_by_id: dict[str, VitalityInputs]) -> dict[str, float]:
    """Apply the formula to a set of nodes and persist the new vitality."""
    results: dict[str, float] = {}
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        for nid in node_ids:
            vi = inputs_by_id[nid]
            v = compute(vi)
            cur.execute(
                "UPDATE nodes SET vitality = ?, updated_at = ? WHERE id = ?",
                (v, now_iso, nid),
            )
            results[nid] = v
        conn.commit()
    return results
