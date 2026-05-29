"""Stop hook: flush the session transcript into the day buffer for the next dream cycle.

Reads the session transcript path from stdin payload and appends the load-bearing
exchanges to today's JSONL buffer through the sanitisation gate.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unicodedata
from pathlib import Path

# ── Re-exec under a deps-capable interpreter if launched with a bare `python` ──
# Guarded by __main__ so importing this module (e.g. under pytest) never re-execs.
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


def _normalize(text: str) -> str:
    """Fold accented characters to ASCII for token matching, lowercase."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii").lower()


# Canonical (unaccented) forms only — _normalize() handles the rest.
# Each entry matches both the accented original and its ASCII fold.
LOAD_BEARING_TOKENS: tuple[str, ...] = (
    # decisions / rules
    "decide",
    "decision",
    "regle",        # règle
    "convention",
    "il faut",
    "ne plus",
    "ne pas",
    "toujours",
    "jamais",
    # corrections / errors
    "correction",
    "error",
    "erreur",
    "bug",
    "retry",
    "fix",
    # architecture / tech
    "architecture",
    "refactor",
    "migration",
    "deprec",       # deprecate / déprécié
    # process
    "procedure",    # procédure
    "workflow",
    "process",
    # people / project
    "objectif",
    "priorite",     # priorité
    "deadline",
    "livrable",
)


def _is_load_bearing(text: str) -> bool:
    normalized = _normalize(text)
    return any(tok in normalized for tok in LOAD_BEARING_TOKENS)


def main() -> int:
    if append_event is None:
        print(json.dumps({"hook": "stop", "status": "unavailable"}))
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath")
    session_id = payload.get("session_id") or payload.get("sessionId") or "unknown"
    if not transcript_path or not Path(transcript_path).exists():
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
            if not content or not _is_load_bearing(content):
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
            })
            appended += 1

    print(json.dumps({"hook": "stop", "status": "ok", "appended": appended}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
