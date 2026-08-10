"""Dream self-diagnostic.

Checks everything that has to line up for the MCP host to attach to the dream
server: the interpreter, the dependencies, DREAM_HOME, the SQLite schema, the
metrics port and the registration in claude_desktop_config.json. It is
stdlib-only so it still runs when the dependencies are missing (which is exactly
when you need it).

    python doctor.py
    python mcp_server.py --doctor

Exit code is 0 when nothing FAILED, 1 otherwise. WARN never fails the run.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
DB_PATH = DREAM_HOME / "pgt.sqlite"

# Distribution name -> import name, for the mandatory runtime stack.
REQUIRED = {
    "mcp": "mcp",
    "httpx": "httpx",
    "lancedb": "lancedb",
    "pyarrow": "pyarrow",
    "sentence-transformers": "sentence_transformers",
    "rank-bm25": "rank_bm25",
    "networkx": "networkx",
    "numpy": "numpy",
    "cryptography": "cryptography",
    "apscheduler": "apscheduler",
    "prometheus-client": "prometheus_client",
    "redis": "redis",
    "psutil": "psutil",
    "pandas": "pandas",
}

EXPECTED_TABLES = {"nodes", "edges", "ledger", "ledger_state", "hitl_queue"}

_RESULTS: list[tuple[str, str, str]] = []  # (level, label, detail)


def _record(level: str, label: str, detail: str = "") -> None:
    _RESULTS.append((level, label, detail))


def check_interpreter() -> None:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} at {sys.executable}"
    if (v.major, v.minor) >= (3, 11):
        _record("PASS", "Python interpreter", detail)
    else:
        _record("FAIL", "Python interpreter", f"{detail} (need 3.11+)")


def check_dependencies() -> None:
    missing = []
    for dist, mod in REQUIRED.items():
        if importlib.util.find_spec(mod) is None:
            missing.append(dist)
    if not missing:
        _record("PASS", "Dependencies", f"all {len(REQUIRED)} present")
    else:
        hint = f'"{sys.executable}" -m pip install ' + " ".join(missing)
        _record("FAIL", "Dependencies", "missing: " + ", ".join(missing) + " -> " + hint)


def check_dream_home() -> None:
    if DREAM_HOME.exists():
        _record("PASS", "DREAM_HOME", str(DREAM_HOME))
    else:
        _record("WARN", "DREAM_HOME", f"{DREAM_HOME} absent (created on first run)")


def check_schema() -> None:
    if not DB_PATH.exists():
        _record("WARN", "SQLite schema", f"{DB_PATH} absent (created on first run)")
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        tables = {r[0] for r in rows}
        missing = EXPECTED_TABLES - tables
        if not missing:
            _record("PASS", "SQLite schema", f"{len(tables)} tables in {DB_PATH.name}")
        else:
            _record("FAIL", "SQLite schema", "missing tables: " + ", ".join(sorted(missing)))
    except sqlite3.Error as exc:
        _record("FAIL", "SQLite schema", f"cannot read {DB_PATH}: {exc}")


def check_metrics_port() -> None:
    port = int(os.environ.get("DREAM_METRICS_PORT", "9464"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        bound = s.connect_ex(("127.0.0.1", port)) == 0
    if bound:
        _record("WARN", "Metrics port", f"{port} already in use (a sibling instance holds it; harmless)")
    else:
        _record("PASS", "Metrics port", f"{port} free")


def _config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    # macOS / Linux fallbacks
    mac = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    return mac if mac.exists() else Path.home() / ".config/Claude/claude_desktop_config.json"


def check_registration() -> None:
    cfg = _config_path()
    if not cfg.exists():
        _record("WARN", "Manual registration", f"{cfg} absent (plugin install only)")
        return
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _record("FAIL", "Manual registration", f"cannot parse {cfg}: {exc}")
        return
    entry = (data.get("mcpServers") or {}).get("dream")
    if not entry:
        _record("WARN", "Manual registration", "no mcpServers.dream entry")
        return
    cmd = entry.get("command", "")
    args = entry.get("args") or []
    problems = []
    resolved_cmd = cmd
    if cmd and os.path.sep not in cmd and not Path(cmd).is_absolute():
        # bare "python" -> rely on PATH; flag because it is the classic failure
        problems.append(f'command "{cmd}" is not an absolute path (PATH-dependent)')
    elif cmd and not Path(cmd).exists():
        problems.append(f'command "{cmd}" does not exist')
    script = Path(args[0]) if args else None
    if script and not script.exists():
        problems.append(f'script "{script}" does not exist')
    if problems:
        _record("FAIL", "Manual registration", "; ".join(problems))
    else:
        _record("PASS", "Manual registration", f"{resolved_cmd} -> {script}")


def check_startup_cost() -> None:
    """Import the server module and time it. A cold import over a few seconds is
    the signature of the eager-ML-import regression that broke the handshake."""
    spec = importlib.util.find_spec("mcp_server")
    if spec is None and str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if importlib.util.find_spec("mcp") is None:
        _record("WARN", "Startup cost", "skipped (mcp not installed)")
        return
    start = time.perf_counter()
    try:
        importlib.import_module("mcp_server")
    except Exception as exc:
        _record("FAIL", "Startup cost", f"import mcp_server failed: {exc}")
        return
    elapsed = time.perf_counter() - start
    detail = f"import took {elapsed:.1f}s"
    if elapsed < 5:
        _record("PASS", "Startup cost", detail)
    elif elapsed < 30:
        _record("WARN", "Startup cost", detail + " (slow; risks handshake timeout under load)")
    else:
        _record("FAIL", "Startup cost", detail + " (will time out the MCP handshake)")


def run_quick() -> list[str]:
    """Cheap subset of checks (no ML import, no server import) for the
    SessionStart self-check. Returns human-readable problem strings; an empty
    list means nothing drifted."""
    _RESULTS.clear()
    for check in (check_interpreter, check_dependencies, check_dream_home, check_schema, check_registration):
        try:
            check()
        except Exception as exc:
            _record("FAIL", check.__name__, f"check raised {exc!r}")
    problems = [f"{label}: {detail}" for level, label, detail in _RESULTS if level == "FAIL"]
    _RESULTS.clear()
    return problems


def _pip_install(dists: list[str]) -> str:
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", *dists],
        capture_output=True, text=True, timeout=600,
    )
    return "installed " + " ".join(dists) if proc.returncode == 0 else f"pip failed: {proc.stderr[-200:]}"


def _write_nightly_cmd() -> str:
    """Rewrite the scheduled-task wrapper against the interpreter and the script
    directory actually running right now. The recurring Windows breakage was a
    nightly.cmd left pointing into a plugin cache that a later update deleted:
    the task then failed silently every night."""
    target = SCRIPTS_DIR / "nightly.cmd"
    body = (
        "@echo off\r\n"
        f'set "DREAM_HOME={DREAM_HOME}"\r\n'
        f'"{sys.executable}" "{SCRIPTS_DIR / "scheduler.py"}" --once >> "{DREAM_HOME / "logs" / "nightly.log"}" 2>&1\r\n'
    )
    if target.exists() and target.read_text(encoding="utf-8", errors="replace") == body:
        return ""
    target.write_text(body, encoding="utf-8")
    return f"rewrote {target}"


def repair() -> list[str]:
    """Fix what the checks above can detect, and only that.

    Deliberately narrow: it recreates missing state, reinstalls missing
    dependencies, repoints the nightly wrapper and clears a stale breaker. It
    never edits claude_desktop_config.json (that is setup_windows.py's job and
    it needs a Claude Desktop restart anyway) and never touches memory content.
    """
    done: list[str] = []
    problems = run_quick()
    blob = " ".join(problems)

    if not DREAM_HOME.exists():
        for sub in ("", "buffer", "logs", "topics", "archive/cold", "rejected", "keys"):
            (DREAM_HOME / sub).mkdir(parents=True, exist_ok=True)
        done.append(f"created {DREAM_HOME} and its subdirectories")

    if "Dependencies" in blob:
        missing = [d for d, m in REQUIRED.items() if importlib.util.find_spec(m) is None]
        if missing:
            done.append(_pip_install(missing))

    if "SQLite schema" in blob or not DB_PATH.exists():
        try:
            sys.path.insert(0, str(SCRIPTS_DIR))
            import db_init

            db_init.init()
            done.append("applied the SQLite schema (db_init)")
        except Exception as exc:  # noqa: BLE001
            done.append(f"db_init failed: {exc}")

    try:
        note = _write_nightly_cmd()
        if note:
            done.append(note)
    except Exception as exc:  # noqa: BLE001
        done.append(f"nightly.cmd rewrite failed: {exc}")

    # A stale breaker outlives its cause: the mode is persisted, so a machine
    # that once tripped SECURISE stays there until three green probes land, and
    # in SECURISE the cycle that would produce them refuses to run.
    if not run_quick():
        try:
            sys.path.insert(0, str(SCRIPTS_DIR))
            import circuit_breaker

            if circuit_breaker._load().mode != "NORMAL":
                circuit_breaker.force_mode("NORMAL")
                done.append("cleared a stale circuit breaker (-> NORMAL)")
        except Exception as exc:  # noqa: BLE001
            done.append(f"breaker reset failed: {exc}")

    return done


def main() -> int:
    fix = "--fix" in sys.argv
    print("=" * 60)
    print("  Dream doctor" + ("  (--fix)" if fix else ""))
    print("=" * 60)

    if fix:
        for line in repair() or ["nothing to repair"]:
            print(f"  [FIX ] {line}")
        print("-" * 60)
        _RESULTS.clear()
    for check in (
        check_interpreter,
        check_dependencies,
        check_dream_home,
        check_schema,
        check_metrics_port,
        check_registration,
        check_startup_cost,
    ):
        try:
            check()
        except Exception as exc:  # a diagnostic must never crash
            _record("FAIL", check.__name__, f"check raised {exc!r}")

    width = max(len(label) for _, label, _ in _RESULTS)
    for level, label, detail in _RESULTS:
        print(f"  [{level:4}] {label.ljust(width)}  {detail}")
    print("=" * 60)

    failures = sum(1 for level, _, _ in _RESULTS if level == "FAIL")
    if failures:
        print(f"  {failures} FAIL — fix the items above, then restart Claude Desktop.")
        return 1
    print("  No blocking issue. Restart Claude Desktop if dream was not attached.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
