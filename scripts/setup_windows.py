"""
setup_windows.py — Register Dream on Windows.

stdlib-only: safe to run before dream deps are installed.

Actions
-------
1. Detect the Python 3.12 executable that has dream deps installed.
2. Inject mcpServers.dream into %APPDATA%\\Claude\\claude_desktop_config.json.
3. Inject SessionStart + Stop hooks into %USERPROFILE%\\.claude\\settings.json.
4. Create a Windows Task Scheduler task "Dream\\NightlyCycle" at 02:05.

Usage
-----
    python setup_windows.py [--dream-home PATH] [--plugin-root PATH] [--dry-run]

--dream-home   defaults to $DREAM_HOME or %USERPROFILE%\\.dream
--plugin-root  defaults to the parent of this file's directory (the plugin root)
--dry-run      print what would be written, touch nothing
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Python 3.12 detection
# ---------------------------------------------------------------------------

_CANDIDATE_PATHS = [
    pathlib.Path(os.environ.get("LOCALAPPDATA", "C:\\")) / "Programs/Python/Python312/python.exe",
    pathlib.Path("C:/Python312/python.exe"),
    pathlib.Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Python312/python.exe",
    pathlib.Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / "Python312/python.exe",
]


def find_python312() -> Optional[str]:
    """Return the path to a Python 3.12 executable, or None."""
    # Prefer the py.exe launcher — works on any standard Windows install
    for flag in ("-3.12", "-3.12-64", "-3.12-32"):
        try:
            r = subprocess.run(
                ["py", flag, "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                exe = r.stdout.strip()
                if pathlib.Path(exe).exists():
                    return exe
        except FileNotFoundError:
            break  # py launcher not installed

    # Fallback: well-known install locations
    for p in _CANDIDATE_PATHS:
        if p.exists():
            return str(p)
    return None


def verify_dream_deps(python_exe: str) -> bool:
    """Return True if the given Python has all mandatory dream deps."""
    probe = "import lancedb, mcp, sentence_transformers, networkx, cryptography, apscheduler"
    r = subprocess.run(
        [python_exe, "-c", probe],
        capture_output=True, text=True, timeout=20,
    )
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Schema bootstrap (stdlib-only, idempotent)
# ---------------------------------------------------------------------------

def apply_schema(python_exe: str, dream_home: pathlib.Path, dry_run: bool) -> bool:
    """Create the SQLite tables via db_init.py (no sqlite3 CLI dependency)."""
    db_init = pathlib.Path(__file__).parent / "db_init.py"
    env = dict(os.environ, DREAM_HOME=str(dream_home))
    if dry_run:
        print(f"  DRY-RUN: would run: {python_exe} {db_init}")
        return True
    r = subprocess.run([python_exe, str(db_init)], capture_output=True, text=True, env=env)
    if r.returncode == 0:
        print(f"  OK: {r.stdout.strip()}")
        return True
    print(f"  WARN: db_init exited {r.returncode}: {r.stderr.strip()}")
    return False


# ---------------------------------------------------------------------------
# Shared MCP env — mirrors config/mcp_servers.json so the manual and
# plugin-manager install paths register the same environment.
# ---------------------------------------------------------------------------

def _mcp_env(dream_home: pathlib.Path) -> dict:
    env = os.environ
    return {
        "DREAM_HOME": str(dream_home),
        "PYTHONPATH": str(dream_home / "scripts"),
        "DREAM_REDIS_HOST": env.get("DREAM_REDIS_HOST", "127.0.0.1"),
        "DREAM_REDIS_PORT": env.get("DREAM_REDIS_PORT", "6379"),
        "DREAM_CONSOLIDATION_MODEL": env.get("DREAM_CONSOLIDATION_MODEL", "gemma4:26b"),
        "DREAM_COUNTERFACTUAL_MODEL": env.get("DREAM_COUNTERFACTUAL_MODEL", "gemma4:26b"),
    }


# ---------------------------------------------------------------------------
# Step 2 — claude_desktop_config.json
# ---------------------------------------------------------------------------

def inject_mcp_config(
    python_exe: str,
    dream_home: pathlib.Path,
    dry_run: bool,
) -> bool:
    config_path = pathlib.Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    if not config_path.parent.exists():
        print(f"  SKIP: {config_path.parent} does not exist — is Claude Desktop installed?")
        return False

    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  WARN: could not parse existing config ({exc}), starting fresh")

    mcp_entry = {
        "command": python_exe,
        "args": [str(dream_home / "scripts" / "mcp_server.py")],
        "env": _mcp_env(dream_home),
    }
    config.setdefault("mcpServers", {})["dream"] = mcp_entry

    if dry_run:
        print(f"  DRY-RUN: would write to {config_path}")
        print("  Entry: " + json.dumps(mcp_entry, ensure_ascii=False))
        return True

    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  OK: mcpServers.dream written to {config_path}")
    return True


# ---------------------------------------------------------------------------
# Step 3 — ~/.claude/settings.json hooks
# ---------------------------------------------------------------------------

def inject_hooks(
    python_exe: str,
    dream_home: pathlib.Path,
    dry_run: bool,
) -> bool:
    settings_path = pathlib.Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  WARN: could not parse existing settings ({exc}), starting fresh")

    env = {
        "DREAM_HOME": str(dream_home),
        "PYTHONPATH": str(dream_home / "scripts"),
    }

    def _hook(script: str, timeout: int) -> dict:
        return {
            "type": "command",
            "command": f'"{python_exe}" "{dream_home / "scripts" / script}"',
            "timeout": timeout,
            "env": env,
        }

    hooks = settings.setdefault("hooks", {})
    hooks["SessionStart"] = [{"matcher": "*", "hooks": [_hook("hook_session_start.py", 10)]}]
    hooks["Stop"] = [{"matcher": "*", "hooks": [_hook("hook_stop.py", 60)]}]

    if dry_run:
        print(f"  DRY-RUN: would write hooks to {settings_path}")
        return True

    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  OK: SessionStart + Stop hooks written to {settings_path}")
    return True


# ---------------------------------------------------------------------------
# Step 4 — Windows Task Scheduler
# ---------------------------------------------------------------------------

def register_task_scheduler(
    python_exe: str,
    dream_home: pathlib.Path,
    dry_run: bool,
) -> bool:
    scheduler_py = dream_home / "scripts" / "scheduler.py"
    task_run = f'"{python_exe}" "{scheduler_py}" --once'
    cmd = [
        "schtasks", "/Create",
        "/TN", r"Dream\NightlyCycle",
        "/TR", task_run,
        "/SC", "DAILY",
        "/ST", "02:05",
        "/RL", "HIGHEST",   # Run with highest available privileges
        "/F",               # Force-overwrite if task already exists
    ]

    if dry_run:
        print(f"  DRY-RUN: would run: {' '.join(cmd)}")
        return True

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        print(r"  OK: Task Scheduler task 'Dream\NightlyCycle' created at 02:05")
        return True

    print(f"  WARN: schtasks exited {r.returncode}: {r.stderr.strip()}")
    print(f"  Manual command to create task:\n    {' '.join(cmd)}")
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register the Dream plugin on Windows (MCP config, hooks, Task Scheduler)."
    )
    parser.add_argument(
        "--dream-home",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("DREAM_HOME", pathlib.Path.home() / ".dream")),
        help="DREAM_HOME directory (default: ~/.dream)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written, touch nothing.",
    )
    args = parser.parse_args()
    dream_home: pathlib.Path = args.dream_home

    print("=" * 55)
    print("  Dream Windows Registration")
    print("=" * 55)
    print(f"  DREAM_HOME : {dream_home}")
    print(f"  dry-run    : {args.dry_run}")
    print()

    # Step 1 — Python 3.12
    print("[1/4] Detecting Python 3.12 with dream deps...")
    python_exe = find_python312()
    if not python_exe:
        print("  ERROR: Python 3.12 not found.")
        print("  Install from https://www.python.org/downloads/ and ensure '3.12' is in PATH.")
        sys.exit(1)
    print(f"  Found : {python_exe}")
    if not verify_dream_deps(python_exe):
        print(f"  WARN  : dream deps not installed in {python_exe}")
        print(f"  Run   : {python_exe} -m pip install lancedb mcp sentence-transformers")
        print(f"          networkx cryptography psutil rank-bm25 apscheduler fastmcp")
        print("  Continuing setup — deps can be installed after registration.")

    # Schema bootstrap
    print("\n[1b] Applying SQLite schema (db_init.py)...")
    apply_schema(python_exe, dream_home, args.dry_run)

    # Step 2 — MCP config
    print("\n[2/4] Injecting mcpServers.dream into claude_desktop_config.json...")
    inject_mcp_config(python_exe, dream_home, args.dry_run)

    # Step 3 — Hooks
    print("\n[3/4] Injecting SessionStart + Stop hooks into ~/.claude/settings.json...")
    inject_hooks(python_exe, dream_home, args.dry_run)

    # Step 4 — Task Scheduler
    print(r"\n[4/4] Creating Windows Task Scheduler task 'Dream\NightlyCycle' at 02:05...")
    register_task_scheduler(python_exe, dream_home, args.dry_run)

    print()
    print("=" * 55)
    if args.dry_run:
        print("  Dry-run complete — no files were modified.")
    else:
        print("  Registration complete.")
        print("  Restart Claude Desktop to activate the Dream MCP server.")
    print(f"  Manual cycle: {python_exe} {dream_home / 'scripts' / 'scheduler.py'} --once")
    print("=" * 55)


if __name__ == "__main__":
    main()
