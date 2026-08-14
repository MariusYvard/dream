"""Append-only daily JSONL buffer for raw events.

The buffer is the source of truth for the nightly consolidation cycle. Writes
are sanitised through `sanitize_local.sanitize` before they hit disk.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from sanitize_local import sanitize, sanitize_regex_only

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
BUFFER_DIR = DREAM_HOME / "buffer"
_LOCK = threading.Lock()


def _today_path() -> Path:
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    return BUFFER_DIR / f"{dt.date.today().isoformat()}.jsonl"


def append_event(payload: dict[str, Any], *, full_llm: bool = True) -> dict[str, Any]:
    """Sanitise and append an event. Returns the persisted record.

    full_llm=False uses the deterministic regex-only redaction: fast and
    LLM-free, for the Stop hook critical path. The nightly cycle re-runs the
    full LLM pass (see scheduler._upgrade_sanitisation) before the content
    reaches topics or the graph. The record carries meta.sanitised = "regex"
    or "llm" so the cycle knows which events still need the heavy pass.
    """
    raw_content = payload.get("content", "")
    sanitised = sanitize(raw_content) if full_llm else sanitize_regex_only(raw_content)

    record = {
        "id": payload.get("id") or str(uuid.uuid4()),
        "type": payload.get("type", "fact"),
        "content": sanitised.text,
        "validity": payload.get("validity") or {
            "from": dt.datetime.now(dt.timezone.utc).isoformat(),
            "to": None,
            "confidence": payload.get("confidence", 0.85),
        },
        "meta": {
            **(payload.get("meta") or {}),
            "project": payload.get("project"),
            "input_sha": sanitised.input_sha,
            "output_sha": sanitised.output_sha,
            "replacements": sanitised.replacements,
            "sanitiser_runtime_ms": sanitised.runtime_ms,
            "sanitised": "llm" if full_llm else "regex",
        },
    }

    with _LOCK:
        with _today_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        _unmark(dt.date.today())  # new content reopens the day for consolidation
    return record


def iter_buffer(day: dt.date | None = None) -> list[dict[str, Any]]:
    target = BUFFER_DIR / f"{(day or dt.date.today()).isoformat()}.jsonl"
    if not target.exists():
        return []
    with target.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --- catch-up bookkeeping -------------------------------------------------
# The cycle used to read today's buffer and nothing else, so a night the
# machine was asleep was lost for good. A day is listed here once it has been
# consolidated, and drops off the list again as soon as new content lands in
# it. Plain text file on purpose: no migration, and readable by eye.
_CONSOLIDATED = BUFFER_DIR / ".consolidated"


def _read_marks() -> set[str]:
    if not _CONSOLIDATED.exists():
        return set()
    return {line.strip() for line in _CONSOLIDATED.read_text(encoding="utf-8").splitlines() if line.strip()}


def _write_marks(marks: set[str]) -> None:
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    _CONSOLIDATED.write_text("\n".join(sorted(marks)) + "\n", encoding="utf-8")


def _unmark(day: dt.date) -> None:
    marks = _read_marks()
    if day.isoformat() in marks:
        _write_marks(marks - {day.isoformat()})


def mark_consolidated(day: dt.date) -> None:
    _write_marks(_read_marks() | {day.isoformat()})


def buffer_days() -> list[dt.date]:
    if not BUFFER_DIR.exists():
        return []
    days = []
    for path in BUFFER_DIR.glob("*.jsonl"):
        try:
            days.append(dt.date.fromisoformat(path.stem))
        except ValueError:
            continue  # not a dated buffer file
    return sorted(days)


def pending_days(*, max_days: int = 14) -> list[dt.date]:
    """Days with content still waiting for a cycle, oldest first.

    Capped so that coming back to a machine left off for a month does not turn
    the first night into an unbounded run.
    """
    marks = _read_marks()
    pending = [d for d in buffer_days() if d.isoformat() not in marks]
    return pending[-max_days:]
