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
    return record


def iter_buffer(day: dt.date | None = None) -> list[dict[str, Any]]:
    target = BUFFER_DIR / f"{(day or dt.date.today()).isoformat()}.jsonl"
    if not target.exists():
        return []
    with target.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
