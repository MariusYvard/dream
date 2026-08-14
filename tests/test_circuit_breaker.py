"""Unit tests for circuit_breaker."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="dream_cb_test_")
os.environ["DREAM_HOME"] = _TMP

sys.path.insert(0, str(Path(__file__).parent.parent / "dream" / "scripts"))

import circuit_breaker  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(circuit_breaker, "DREAM_HOME", tmp_path)
    monkeypatch.setattr(circuit_breaker, "STATE_PATH", tmp_path / "circuit.json")
    yield


def _healthy_probe(**kwargs) -> circuit_breaker.HealthProbe:
    defaults = dict(latency_p95_ms=100, consensus_rate_24h=0.9, vitality_avg=0.7, ram_peak_mb=4000, ledger_merkle_ok=True)
    defaults.update(kwargs)
    return circuit_breaker.HealthProbe(**defaults)


class TestEvaluate:
    def test_healthy_probe_stays_normal(self):
        state = circuit_breaker.evaluate(_healthy_probe())
        assert state.mode == "NORMAL"

    def test_high_latency_triggers_conservateur(self):
        state = circuit_breaker.evaluate(_healthy_probe(latency_p95_ms=600))
        assert state.mode == "CONSERVATEUR"

    def test_low_consensus_triggers_conservateur(self):
        state = circuit_breaker.evaluate(_healthy_probe(consensus_rate_24h=0.5))
        assert state.mode == "CONSERVATEUR"

    def test_failed_merkle_triggers_securise(self):
        state = circuit_breaker.evaluate(_healthy_probe(ledger_merkle_ok=False))
        assert state.mode == "SECURISE"

    def test_high_ram_triggers_securise(self):
        state = circuit_breaker.evaluate(_healthy_probe(ram_peak_mb=16000))
        assert state.mode == "SECURISE"

    def test_low_vitality_never_trips_the_breaker(self):
        """The deadlock regression, both halves of it.

        A node nobody reads sits at exactly 0.30 (decay term alone), so the old
        `vitality_avg < 0.4` trigger fired SECURISE, consolidation refused to
        run, nothing was ever read, and vitality could never leave 0.30. Cold
        memory is not unsafe memory: vitality is a metric now, not a gate.
        """
        for vitality, nodes in ((0.0, 0), (0.30, 11), (0.1, 1)):
            state = circuit_breaker.evaluate(
                _healthy_probe(vitality_avg=vitality, active_nodes=nodes)
            )
            assert state.mode == "NORMAL", f"vitality={vitality} nodes={nodes}"

    def test_real_safety_signals_still_trip(self):
        """Removing the vitality gate must not soften the genuine ones."""
        assert circuit_breaker.evaluate(
            _healthy_probe(vitality_avg=0.3, ledger_merkle_ok=False)
        ).mode == "SECURISE"

    def test_recovery_requires_3_green_probes(self):
        # Drive into CONSERVATEUR first.
        circuit_breaker.evaluate(_healthy_probe(latency_p95_ms=600))
        # One green probe is not enough.
        s1 = circuit_breaker.evaluate(_healthy_probe())
        assert s1.mode == "CONSERVATEUR"
        s2 = circuit_breaker.evaluate(_healthy_probe())
        assert s2.mode == "CONSERVATEUR"
        # Third green probe promotes back.
        s3 = circuit_breaker.evaluate(_healthy_probe())
        assert s3.mode == "NORMAL"

    def test_degradation_is_immediate(self):
        """Degradation never requires multiple probes."""
        circuit_breaker.evaluate(_healthy_probe())  # NORMAL
        s = circuit_breaker.evaluate(_healthy_probe(latency_p95_ms=600))
        assert s.mode == "CONSERVATEUR"


class TestForceMode:
    def test_force_valid_mode(self):
        result = circuit_breaker.force_mode("SECURISE")
        assert result["status"] == "ok"
        assert result["mode"] == "SECURISE"
        state = circuit_breaker._load()
        assert state.mode == "SECURISE"
        assert state.green_streak == 0

    def test_force_invalid_mode_returns_error(self):
        result = circuit_breaker.force_mode("BROKEN")
        assert result["status"] == "invalid_mode"
        assert "valid_modes" in result

    def test_force_mode_resets_green_streak(self):
        # Build up a streak first.
        circuit_breaker.evaluate(_healthy_probe(latency_p95_ms=600))
        circuit_breaker.evaluate(_healthy_probe())
        circuit_breaker.evaluate(_healthy_probe())
        circuit_breaker.force_mode("NORMAL")
        state = circuit_breaker._load()
        assert state.green_streak == 0
