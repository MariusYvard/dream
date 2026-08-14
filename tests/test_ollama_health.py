"""Tests for the Ollama reachability probe.

Intent: the probe must never raise and must map an unreachable daemon to False,
because the scheduler relies on a False to refuse a cycle cleanly instead of
churning through per-cluster exceptions and finishing "ok" with nothing done.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "dream" / "scripts"))

import ollama_health


def test_unreachable_returns_false(monkeypatch):
    # Point at a port nothing listens on: connection refused, not an exception.
    monkeypatch.setattr(ollama_health, "OLLAMA_BASE", "http://127.0.0.1:1")
    assert ollama_health.ollama_up(timeout=0.5) is False


def test_non_200_returns_false(monkeypatch):
    class _Resp:
        status_code = 503

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp()

    monkeypatch.setattr(ollama_health.httpx, "Client", _Client)
    assert ollama_health.ollama_up() is False


def test_reachable_returns_true(monkeypatch):
    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp()

    monkeypatch.setattr(ollama_health.httpx, "Client", _Client)
    assert ollama_health.ollama_up() is True
