"""Stop hook: flush the session transcript into the day buffer for the next dream cycle.

Reads the session transcript path from the stdin payload and appends candidate
exchanges to today's JSONL buffer through the fast, regex-only sanitisation
gate. The heavy LLM redaction and the real load-bearing decision both run later
in the nightly cycle, so session exit never blocks on the local model. A
heartbeat line is written on every run, including the no-op paths, so a silent
capture outage is visible in health_check.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unicodedata
from pathlib import Path

# ── Re-exec under a deps-capable interpreter if launched with a bare `python` ──
sys.path.insert(0, str(Path(__file__).parent))
if __name__ == "__main__":
    try:
        from interpreter import reexec_if_needed

        reexec_if_needed(__file__)
    except Exception:
        pass

try:
    from dream_buffer import append_event  # type: ignore
except Exception:
    append_event = None  # type: ignore

try:
    from load_bearing import is_candidate as _is_candidate  # type: ignore
except Exception:
    _is_candidate = None  # type: ignore

try:
    import observability as _obs  # type: ignore
except Exception:
    _obs = None  # type: ignore


def _beat(status: str, **extra) -> None:
    if _obs is not None:
        try:
            _obs.beat("stop", status, **extra)
        except Exception:
            pass


def _normalize(text: str) -> str:
    """Fold accented characters to ASCII for token matching, lowercase."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii").lower()


# Fallback lexical filter, used only when the load_bearing module is unavailable.
LOAD_BEARING_TOKENS: tuple[str, ...] = (
    "decide", "decision", "regle", "convention", "il faut", "ne plus", "ne pas",
    "toujours", "jamais", "correction", "error", "erreur", "bug", "retry", "fix",
    "architecture", "refactor", "migration", "deprec", "procedure", "workflow",
    "process", "objectif", "priorite", "deadline", "livrable",
)


def _is_load_bearing(text: str) -> bool:
    normalized = _normalize(text)
    return any(tok in normalized for tok in LOAD_BEARING_TOKENS)


def _candidate(text: str) -> bool:
    """Cheap recall-oriented pre-filter. Uses the shared classifier's fast path
    when available, else the local lexical fallback. Never calls the LLM."""
    if _is_candidate is not None:
        try:
            return _is_candidate(text)
        except Exception:
            pass
    return bool(text) and _is_load_bearing(text)


def main() -> int:
    if append_event is None:
        _beat("unavailable")
        print(json.dumps({"hook": "stop", "status": "unavailable"}))
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath")
    session_id = payload.get("session_id") or payload.get("sessionId") or "unknown"
    if not transcript_path or not Path(transcript_path).exists():
        _beat("no_transcript", session=session_id)
        print(json.dumps({"hook": "stop", "status": "no_transcript"}))
        return 0

    appended = 0
    with Path(transcript_path).open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            content = rec.get("content") or rec.get("text") or ""
            if isinstance(content, list):
                content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
            if not content or not _candidate(content):
                continue
            append_event({
                "type": "fact",
                "content": content[:4000],
                "validity": {
                    "from": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "to": None,
                    "confidence": 0.6,
                },
                "meta": {"source_session": session_id},
            }, full_llm=False)
            appended += 1

    _beat("ok", appended=appended, session=session_id)
    print(json.dumps({"hook": "stop", "status": "ok", "appended": appended}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
