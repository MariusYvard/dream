"""Lightweight Ollama reachability probe.

Used by the scheduler to refuse a consolidation cycle cleanly when the local
Ollama daemon is down (instead of churning through per-cluster exceptions and
finishing with an "ok" status but zero consolidation), and by health_check to
surface the daemon state.

stdlib + httpx only. Never raises.
"""
from __future__ import annotations

import os

import httpx

OLLAMA_BASE = os.environ.get("DREAM_OLLAMA_URL", "http://127.0.0.1:11434")


def ollama_up(timeout: float = 2.0) -> bool:
    """Return True if the local Ollama daemon answers GET /api/tags with 200."""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{OLLAMA_BASE}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False
