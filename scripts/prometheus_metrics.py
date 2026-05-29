"""Prometheus metrics exposed on 127.0.0.1:9464 (configurable)."""
from __future__ import annotations

import logging
import os
from collections import deque

from prometheus_client import Counter, Gauge, Histogram, start_http_server

import cache_layer

log = logging.getLogger("dream.metrics")

CYCLE_COMPLETED = Counter("dream_cycle_completed_total", "Dream cycles completed")
CYCLE_FAILED = Counter("dream_cycle_failed_total", "Dream cycles failed", ["phase"])
SANITISE_LATENCY = Histogram("dream_sanitize_latency_ms", "Sanitisation latency", buckets=(50, 100, 200, 300, 500, 800, 1500, 3000))
SANITISE_BREACH = Counter("dream_sanitize_latency_breach_total", "Sanitisation P95 >800 ms breaches")
SEARCH_LATENCY = Histogram("dream_search_latency_ms", "Search latency", buckets=(20, 50, 100, 200, 500, 1000, 2000))
CONSENSUS_SCORE = Histogram("dream_consensus_score", "Consensus score per cluster", buckets=(0.1, 0.3, 0.5, 0.7, 0.85, 0.95, 1.0))
HITL_PENDING = Gauge("dream_hitl_pending", "HITL items awaiting resolution")
RAM_PEAK = Gauge("dream_ram_peak_mb", "Peak RAM observed by the daemon")
VITALITY_AVG = Gauge("dream_vitality_avg", "Average vitality across active nodes")
CIRCUIT_MODE = Gauge("dream_circuit_mode", "Circuit breaker mode encoded 0=NORMAL 1=CONSERVATEUR 2=SECURISE")
LEDGER_OK = Gauge("dream_ledger_merkle_ok", "1 if Merkle root verifies else 0")
OLLAMA_UP = Gauge("dream_ollama_up", "1 if the local Ollama daemon is reachable else 0")

# Rolling window for the search-latency p95 fed to the circuit breaker.
_SEARCH_SAMPLES: deque[float] = deque(maxlen=256)


def serve(port: int | None = None) -> bool:
    """Start the metrics endpoint. Never raises.

    Returns True if the endpoint was started, False if it was disabled or the
    port was already bound (e.g. the MCP server already holds it while the
    nightly scheduler fires). Set DREAM_METRICS_ENABLED=0 to disable entirely
    or DREAM_METRICS_PORT to relocate it.
    """
    if os.environ.get("DREAM_METRICS_ENABLED", "1") == "0":
        log.info("metrics endpoint disabled via DREAM_METRICS_ENABLED=0")
        return False
    resolved = port or int(os.environ.get("DREAM_METRICS_PORT", "9464"))
    try:
        start_http_server(resolved, addr="127.0.0.1")
        log.info("metrics endpoint listening on 127.0.0.1:%d", resolved)
        return True
    except OSError as exc:
        log.warning("metrics endpoint not started on %d: %s", resolved, exc)
        return False


def record_search_latency(ms: float) -> None:
    """Observe a search latency and refresh the p95 cache key."""
    SEARCH_LATENCY.observe(ms)
    _SEARCH_SAMPLES.append(ms)
    ordered = sorted(_SEARCH_SAMPLES)
    idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
    cache_layer.set("metric:latency_p95", ordered[idx], ttl=3600)


def record_sanitise_latency(ms: float) -> None:
    """Observe a sanitisation latency, count P95 budget breaches."""
    SANITISE_LATENCY.observe(ms)
    if ms > 800:
        SANITISE_BREACH.inc()


if __name__ == "__main__":
    import time

    serve()
    while True:
        time.sleep(60)
