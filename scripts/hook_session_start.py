"""SessionStart hook: warm-load the PGT context into the session.

Reads a JSON event on stdin per the Claude Code hook contract and writes a JSON
result to stdout. Failures are non-fatal (exit 0 with an empty bundle). All
errors are logged to $DREAM_HOME/logs/hook_session_start.log, a heartbeat line
is written for every run, and a cheap self-check flags registration drift (dead
paths, missing deps) so a broken install is visible at session start instead of
days later.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

# ── Re-exec under a deps-capable interpreter if launched with a bare `python` ──
sys.path.insert(0, str(Path(__file__).parent))
if __name__ == "__main__":
    try:
        from interpreter import reexec_if_needed

        reexec_if_needed(__file__)
    except Exception:
        pass

# ── Budget, before any dream import ────────────────────────────────────────────
# This hook runs on every Claude Desktop, Claude Code and VS Code session start,
# under a 10 s cancellation deadline, on a machine that may have a gigabyte of
# RAM to spare. It has exactly two ways to take the whole application down with
# it, and both are shut off here rather than trusted to configuration:
#   - DREAM_LIGHT_CONTEXT: load_context stays on the plain path, so it never
#     loads the 2.3 GB sentence-transformer.
#   - DREAM_ALLOW_CLI=0: nothing may spawn `claude -p`. Claude Code cancels a
#     late hook but does not kill what it spawned, so each session start used to
#     leak a ~300 MB orphan until memory ran out.
os.environ["DREAM_LIGHT_CONTEXT"] = "1"
os.environ["DREAM_ALLOW_CLI"] = "0"

# Windows hands this process a cp1252 stdout. The bundle is memory content, so
# it carries arrows, accents and dashes; one of them and the hook dies with
# UnicodeEncodeError after doing all its work. That is why the hook log has
# been empty.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

# ── Dream imports (graceful degradation if the stack is not initialised yet) ──

try:
    sys.path.insert(0, str(Path(__file__).parent))
    from load_context import build_bundle  # type: ignore
    log.info("load_context imported successfully")
except Exception as exc:
    log.warning("load_context import failed: %s", exc, exc_info=True)
    build_bundle = None  # type: ignore

try:
    import observability as _obs  # type: ignore
except Exception:
    _obs = None  # type: ignore


def _beat(status: str, **extra) -> None:
    if _obs is not None:
        try:
            _obs.beat("session_start", status, **extra)
        except Exception:
            pass


def _self_check() -> None:
    """Run the cheap doctor checks (no ML import) and surface any drift, the
    exact failure mode that left a dead registration path unnoticed for days.

    Once a day is enough. It probes fourteen packages and reads the host config
    from disk, which is a second or more of a ten-second budget spent
    re-answering a question whose answer changes on install, not on session.
    """
    stamp = DREAM_HOME / "logs" / ".selfcheck_stamp"
    try:
        if stamp.exists() and (dt.datetime.now().timestamp() - stamp.stat().st_mtime) < 86400:
            return
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(dt.datetime.now(dt.timezone.utc).isoformat(), encoding="utf-8")
    except Exception:
        pass

    try:
        from doctor import run_quick

        problems = run_quick()
        if problems:
            log.warning("self-check drift: %s", problems)
            if _obs is not None:
                _obs.beat("session_start_selfcheck", "warn", problems=problems)
    except Exception as exc:
        log.warning("self-check skipped: %s", exc)


def _resolve_goal() -> str:
    goal_file = DREAM_HOME / "active_goal.txt"
    if goal_file.exists():
        return goal_file.read_text(encoding="utf-8").strip()
    return "default working session"


def main() -> int:
    _self_check()

    if build_bundle is None:
        log.warning("build_bundle unavailable — returning empty context")
        _beat("unavailable")
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
        log.info("context loaded: %d topics, %d tokens", out["topics_loaded"], out["total_tokens"])
    except Exception as exc:
        log.error("build_bundle raised: %s", exc, exc_info=True)
        out = {"hook": "session_start", "status": "error", "error": str(exc)}

    _beat(out.get("status", "ok"), topics=out.get("topics_loaded", 0), tokens=out.get("total_tokens", 0))
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
