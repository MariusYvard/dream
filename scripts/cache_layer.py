"""Hot cache layer. Redis if available, in-memory dict otherwise.

The hot tier holds nodes with V > 0.85 so they are surfaced in sub-millisecond
latency to the load_context skill.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

TTL_SECONDS = 3600

try:
    import redis  # type: ignore

    _REDIS = redis.Redis(host=os.environ.get("DREAM_REDIS_HOST", "127.0.0.1"), port=int(os.environ.get("DREAM_REDIS_PORT", "6379")), db=0, socket_connect_timeout=0.5)
    _REDIS.ping()
    _BACKEND = "redis"
except Exception:
    _REDIS = None
    _BACKEND = "memory"

_MEMORY: dict[str, tuple[float, Any]] = {}
_LOCK = threading.Lock()


def backend() -> str:
    return _BACKEND


def get(key: str) -> Any | None:
    if _REDIS is not None:
        raw = _REDIS.get(key)
        return json.loads(raw) if raw else None
    with _LOCK:
        entry = _MEMORY.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires < time.time():
            _MEMORY.pop(key, None)
            return None
        return value


def set(key: str, value: Any, ttl: int = TTL_SECONDS) -> None:
    if _REDIS is not None:
        _REDIS.setex(key, ttl, json.dumps(value, ensure_ascii=False))
        return
    with _LOCK:
        _MEMORY[key] = (time.time() + ttl, value)


def invalidate(key: str) -> None:
    if _REDIS is not None:
        _REDIS.delete(key)
        return
    with _LOCK:
        _MEMORY.pop(key, None)
