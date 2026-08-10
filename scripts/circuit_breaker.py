"""Three-mode circuit breaker for the dream stack.

Modes: NORMAL, CONSERVATEUR, SECURISE.
Promotion is sticky: 3 consecutive green probes are required to go up.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
STATE_PATH = DREAM_HOME / "circuit.json"

VALID_MODES = {"NORMAL", "CONSERVATEUR", "SECURISE"}


@dataclass
class HealthProbe:
    latency_p95_ms: float
    consensus_rate_24h: float
    vitality_avg: float
    ram_peak_mb: float
    ledger_merkle_ok: bool
    # Reported and exported as a metric, never a trigger. See evaluate().
    active_nodes: int = 1


@dataclass
class CircuitState:
    mode: str  # NORMAL | CONSERVATEUR | SECURISE
    green_streak: int
    updated_at: str


def _load() -> CircuitState:
    if STATE_PATH.exists():
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return CircuitState(**data)
    return CircuitState(mode="NORMAL", green_streak=0, updated_at=dt.datetime.now(dt.timezone.utc).isoformat())


def _save(state: CircuitState) -> None:
    DREAM_HOME.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(asdict(state)), encoding="utf-8")


def evaluate(probe: HealthProbe) -> CircuitState:
    """Map a health probe to a mode.

    vitality_avg is deliberately NOT a trigger. A cold memory is not an unsafe
    memory, and making it one created a deadlock the stack could not leave on
    its own: a node nobody reads sits at exactly 0.30 (the decay term alone,
    see vitality_engine), which is under the old 0.4 threshold, so the breaker
    tripped SECURISE, so consolidation refused to run, so nothing was ever read,
    so vitality stayed at 0.30. Ledger integrity and RAM are the real safety
    signals. Vitality is still probed, still exported to Prometheus, and still
    what dream-health reports, it just no longer blocks the one process able to
    raise it.
    """
    state = _load()
    target = "NORMAL"

    if not probe.ledger_merkle_ok or probe.ram_peak_mb > 15000:
        target = "SECURISE"
    elif probe.latency_p95_ms > 500 or probe.consensus_rate_24h < 0.7:
        target = "CONSERVATEUR"

    if _severity(target) > _severity(state.mode):
        state.mode = target
        state.green_streak = 0
    elif _severity(target) < _severity(state.mode):
        state.green_streak += 1
        if state.green_streak >= 3:
            state.mode = target
            state.green_streak = 0
    else:
        state.green_streak = 0

    state.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    _save(state)
    return state


def force_mode(mode: str) -> dict[str, object]:
    """Public API for manual mode override (replaces direct _save() calls).

    Returns a status dict compatible with the MCP tool response format.
    """
    if mode not in VALID_MODES:
        return {"status": "invalid_mode", "valid_modes": sorted(VALID_MODES)}
    state = _load()
    state.mode = mode
    state.green_streak = 0
    state.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    _save(state)
    return {"status": "ok", "mode": mode}


def _severity(mode: str) -> int:
    return {"NORMAL": 0, "CONSERVATEUR": 1, "SECURISE": 2}[mode]
