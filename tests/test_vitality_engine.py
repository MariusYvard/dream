"""Unit tests for vitality_engine.compute() and tier_for().

These tests encode the *intent* behind the calibrated constants, not just
the current numbers. If you change ALPHA/BETA/GAMMA/DELTA/LAMBDA, some of
these tests will fail — that is by design: stop and verify the calibration.
"""
import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "dream" / "scripts"))

from vitality_engine import (
    ALPHA, BETA, DELTA, GAMMA, LAMBDA,
    VitalityInputs, compute, tier_for,
)

ZERO_VEC = np.zeros(4)
UNIT_VEC = np.array([1.0, 0.0, 0.0, 0.0])


def _inputs(
    last_accessed_days_ago: float | None = None,
    access_count: int = 0,
    co_activation: float = 0.0,
    node_vec: np.ndarray | None = None,
    goals_vec: np.ndarray | None = None,
    contradiction: float = 0.0,
) -> VitalityInputs:
    now = dt.datetime.now(dt.timezone.utc)
    last = (now - dt.timedelta(days=last_accessed_days_ago)) if last_accessed_days_ago is not None else None
    return VitalityInputs(
        last_accessed=last,
        access_count=access_count,
        co_activation_score=co_activation,
        node_embedding=node_vec if node_vec is not None else ZERO_VEC,
        goals_embedding=goals_vec,
        contradiction_weight=contradiction,
    )


class TestDecayTerm:
    def test_fresh_node_has_max_decay(self):
        """A node accessed right now gets the full alpha decay contribution."""
        v = compute(_inputs(last_accessed_days_ago=0))
        expected_decay = ALPHA * math.exp(0)
        assert abs(v - expected_decay) < 0.01

    def test_30_day_old_node_decays(self):
        """After 30 days with no access, the decay term drops significantly."""
        v = compute(_inputs(last_accessed_days_ago=30))
        decay_30 = ALPHA * math.exp(-LAMBDA * 30)
        assert abs(v - decay_30) < 0.01

    def test_never_accessed_treated_as_zero_days(self):
        """last_accessed=None means 0 days elapsed (new node, no penalty)."""
        v_none = compute(_inputs(last_accessed_days_ago=None))
        v_zero = compute(_inputs(last_accessed_days_ago=0))
        assert abs(v_none - v_zero) < 1e-6


class TestUsageTerm:
    def test_high_access_count_raises_vitality(self):
        v_low = compute(_inputs(access_count=1))
        v_high = compute(_inputs(access_count=100))
        assert v_high > v_low

    def test_vitality_bounded_at_one(self):
        v = compute(_inputs(access_count=10_000))
        assert v <= 1.0


class TestGoalAlignment:
    def test_aligned_node_scores_higher(self):
        """A node whose embedding aligns with the active goal beats an orthogonal one."""
        goal = np.array([1.0, 0.0, 0.0, 0.0])
        aligned = np.array([1.0, 0.0, 0.0, 0.0])
        orthogonal = np.array([0.0, 1.0, 0.0, 0.0])
        v_aligned = compute(_inputs(node_vec=aligned, goals_vec=goal))
        v_ortho = compute(_inputs(node_vec=orthogonal, goals_vec=goal))
        assert v_aligned > v_ortho

    def test_no_goals_gives_zero_goal_term(self):
        v_no_goal = compute(_inputs(node_vec=UNIT_VEC, goals_vec=None))
        v_goal = compute(_inputs(node_vec=UNIT_VEC, goals_vec=UNIT_VEC))
        assert v_goal > v_no_goal


class TestContradictionPenalty:
    def test_contradiction_lowers_vitality(self):
        v_clean = compute(_inputs(contradiction=0.0))
        v_contradicted = compute(_inputs(contradiction=1.0))
        assert v_contradicted < v_clean

    def test_heavy_contradiction_cannot_go_below_zero(self):
        v = compute(_inputs(contradiction=1000.0))
        assert v >= 0.0


class TestTierFor:
    def test_hot_threshold(self):
        assert tier_for(0.86) == "hot"
        assert tier_for(0.85) == "active"  # boundary is exclusive

    def test_active_threshold(self):
        assert tier_for(0.40) == "active"
        assert tier_for(0.39) == "dim"

    def test_dim_threshold(self):
        assert tier_for(0.20) == "dim"
        assert tier_for(0.19) == "cold"

    def test_zero_is_cold(self):
        assert tier_for(0.0) == "cold"
