"""SessionStart hook: warm-load the PGT context into the session.

Reads JSON event on stdin per the Claude Code hook contract and writes a JSON
result to stdout. Failures are non-fatal (exit 0 with an empty bundle).
All errors are logged to $DREAM_HOME/logs/hook_session_start.log so silent
failures are diagnosable without digging into Claude's session output.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

# ── Re-exec under a deps-capable interpreter if launched with a bare `python` ──
# (plugin-manager hooks.json uses `python`; this aligns it with the manual path)
# Guarded by __main__ so importing this module (e.g. under pytest) never re-execs.
sys.path.insert(0, str(Path(__file__).parent))
if __name__ == "__main__":
    try:
        from interpreter import reexec_if_needed

        reexec_if_needed(__file__)
    except Exception:
        pass  # resolver unavailable: fall through and degrade gracefully below

# ── Logging setup — before any dream import so even import errors are captured ──

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
_LOG_DIR = DREAM_HOME / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(_LOG_DIR / "hook_session_start.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("dream.hook.session_start")

# ── Dream import (graceful degradation if stack not initialised yet) ──────────

try:
    sys.path.insert(0, str(Path(__file__).parent))
    from load_context import build_bundle  # type: ignore
    log.info("load_context imported successfully")
except Exception as exc:
    log.warning("load_context import failed: %s", exc, exc_info=True)
    build_bundle = None  # type: ignore


def _resolve_goal() -> str:
    goal_file = DREAM_HOME / "active_goal.txt"
    if goal_file.exists():
        return goal_file.read_text(encoding="utf-8").strip()
    return "default working session"


def main() -> int:
    if build_bundle is None:
        log.warning("build_bundle unavailable — returning empty context")
        print(json.dumps({"hook": "session_start", "status": "unavailable"}))
        return 0

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        log.warning("failed to parse stdin payload: %s", exc)
        payload = {}

    goal = payload.get("goal") or _resolve_goal()
    log.info("loading context for goal: %r", goal[:120])

    try:
        bundle = build_bundle(goal_text=goal, token_budget=2000)
        ctx_md = bundle.get("claude_md", "")
        topics = "\n\n".join(f"## {t['name']}\n{t['content']}" for t in bundle.get("topics", []))
        out = {
            "hook": "session_start",
            "status": "ok",
            "additionalContext": f"{ctx_md}\n\n{topics}",
            "topics_loaded": len(bundle.get("topics", [])),
            "total_tokens": bundle.get("total_tokens", 0),
        }
        log.info(
            "context loaded: %d topics, %d tokens",
            out["topics_loaded"],
            out["total_tokens"],
        )
    except Exception as exc:
        log.error("build_bundle raised: %s", exc, exc_info=True)
        out = {"hook": "session_start", "status": "error", "error": str(exc)}

    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
