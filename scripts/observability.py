"""Lightweight, dependency-free observability for the dream stack.

Everything here is stdlib-only and best-effort: a telemetry write must never
raise into a hook, the scheduler or the MCP server. Three signals turn a silent
outage (the kind that ran 16 days unnoticed) into something health_check sees:

    - heartbeat.jsonl : one line every time a hook fires, even when it captured
      nothing. Answers "is capture wired at all".
    - last_cycle.json : the outcome of the most recent nightly cycle, including
      the skipped paths that used to return before any state was written.
    - last_buffer_write : freshness of the day buffer, surfaced by health_check.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
LOG_DIR = DREAM_HOME / "logs"
BUFFER_DIR = DREAM_HOME / "buffer"
HEARTBEAT_PATH = LOG_DIR / "heartbeat.jsonl"
LAST_CYCLE_PATH = LOG_DIR / "last_cycle.json"
_HEARTBEAT_CAP = 500  # bound the tail so the file never grows without limit


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def beat(hook: str, status: str, **extra: Any) -> None:
    """Append one heartbeat line. Best-effort, never raises."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"at": _now(), "hook": hook, "status": status, **extra}
        line = json.dumps(rec, ensure_ascii=False)
        existing: list[str] = []
        if HEARTBEAT_PATH.exists():
            existing = [ln for ln in HEARTBEAT_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
        existing.append(line)
        if len(existing) > _HEARTBEAT_CAP:
            existing = existing[-_HEARTBEAT_CAP:]
        HEARTBEAT_PATH.write_text("\n".join(existing) + "\n", encoding="utf-8")
    except Exception:
        pass


def record_cycle(status: str, metrics: dict[str, Any] | None = None) -> None:
    """Persist the outcome of a cycle run, including the skipped paths."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"at": _now(), "status": status}
        if metrics:
            payload.update(metrics)
        LAST_CYCLE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def read_cycle() -> dict[str, Any]:
    try:
        if LAST_CYCLE_PATH.exists():
            return json.loads(LAST_CYCLE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def last_buffer_write() -> str | None:
    """ISO timestamp of the most recently written day buffer, or None."""
    try:
        files = list(BUFFER_DIR.glob("*.jsonl"))
        if not files:
            return None
        newest = max(files, key=lambda p: p.stat().st_mtime)
        return dt.datetime.fromtimestamp(newest.stat().st_mtime, dt.timezone.utc).isoformat()
    except Exception:
        return None


def buffer_age_hours() -> float | None:
    iso = last_buffer_write()
    if not iso:
        return None
    try:
        ts = dt.datetime.fromisoformat(iso)
        return (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() / 3600.0
    except Exception:
        return None
