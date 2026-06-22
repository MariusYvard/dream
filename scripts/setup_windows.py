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
import shutil
import subprocess
import sys
from typing import Optional

# Windows consoles and redirected pipes may default to cp1252; any Unicode in
# our output then crashes the script (bit the very first smoke tests).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Where THIS copy of the scripts lives, often the plugin manager's cache. That
# cache path is EPHEMERAL: it changes on every plugin update and the old copy
# is deleted, which silently breaks anything registered against it (MCP server
# entry, hooks, nightly task — all bitten in production: "can't open file ...
# scheduler.py"). Setup therefore DEPLOYS a stable copy of the scripts into
# DREAM_HOME/scripts and registers everything against that copy.
SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Python detection (3.11 - 3.13)
# ---------------------------------------------------------------------------

_SUPPORTED = ("3.13", "3.12", "3.11")


def _candidate_paths() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for tag in ("313", "312", "311"):
        out += [
            pathlib.Path(os.environ.get("LOCALAPPDATA", "C:\\")) / f"Programs/Python/Python{tag}/python.exe",
            pathlib.Path(f"C:/Python{tag}/python.exe"),
            pathlib.Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / f"Python{tag}/python.exe",
            pathlib.Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / f"Python{tag}/python.exe",
        ]
    return out


def find_python() -> Optional[str]:
    """Return a usable Python 3.11+ executable.

    Preference order: an interpreter that already has the dream deps, then the
    py.exe launcher, then well-known install paths, then the running
    interpreter as a last resort (so setup never dead-ends).
    """
    # 1. py.exe launcher, newest supported first
    for ver in _SUPPORTED:
        try:
            r = subprocess.run(
                ["py", f"-{ver}", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                exe = r.stdout.strip()
                if exe and pathlib.Path(exe).exists():
                    return exe
        except FileNotFoundError:
            break  # py launcher not installed

    # 2. well-known install locations
    for p in _candidate_paths():
        if p.exists():
            return str(p)

    # 3. last resort: the interpreter running this script
    if sys.version_info[:2] >= (3, 11):
        return sys.executable
    return None


# Backwards-compatible alias.
find_python312 = find_python


def verify_dream_deps(python_exe: str) -> bool:
    """Return True if the given Python has all mandatory dream deps."""
    probe = "import lancedb, mcp, sentence_transformers, networkx, cryptography, apscheduler"
    # sentence_transformers alone takes ~80 s to import on a cold machine; the
    # original 20 s timeout made setup crash in the middle of verification.
    r = subprocess.run(
        [python_exe, "-c", probe],
        capture_output=True, text=True, timeout=300,
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
# Stable script deployment — DREAM_HOME/scripts survives plugin updates
# ---------------------------------------------------------------------------

def deploy_scripts(dream_home: pathlib.Path, dry_run: bool) -> pathlib.Path:
    """Copy the scripts (plus graph_schema.sql and requirements.txt) into
    DREAM_HOME/scripts and return that path.

    Registrations must never point into the plugin manager cache: that path is
    versioned and garbage-collected. DREAM_HOME survives plugin updates, so
    the MCP server, the hooks and the nightly task keep working after one.
    """
    target = dream_home / "scripts"
    if dry_run:
        print(f"  DRY-RUN: would copy {SCRIPTS_DIR} -> {target}")
        return target
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in SCRIPTS_DIR.glob("*.py"):
        shutil.copy2(src, target / src.name)
        copied += 1
    sql = SCRIPTS_DIR / "graph_schema.sql"
    if sql.exists():
        shutil.copy2(sql, target / sql.name)
        copied += 1
    req = SCRIPTS_DIR.parent / "requirements.txt"
    if req.exists():
        shutil.copy2(req, target / "requirements.txt")
        copied += 1
    print(f"  OK: {copied} files deployed to {target}")
    return target


# ---------------------------------------------------------------------------
# Shared MCP env — mirrors config/mcp_servers.json so the manual and
# plugin-manager install paths register the same environment.
# ---------------------------------------------------------------------------

def _mcp_env(dream_home: pathlib.Path, scripts_dir: pathlib.Path) -> dict:
    # Models come from the profile (DREAM_PROFILE=full|lite) so a lite install is
    # one switch: `set DREAM_PROFILE=lite & python setup_windows.py`. Any explicit
    # DREAM_*_MODEL env still overrides the profile.
    import model_profile

    env = os.environ
    return {
        "DREAM_HOME": str(dream_home),
        "PYTHONPATH": str(scripts_dir),
        "DREAM_PROFILE": model_profile.name(),
        "DREAM_REDIS_HOST": env.get("DREAM_REDIS_HOST", "127.0.0.1"),
        "DREAM_REDIS_PORT": env.get("DREAM_REDIS_PORT", "6379"),
        "DREAM_CONSOLIDATION_MODEL": model_profile.consolidation_model(),
        "DREAM_COUNTERFACTUAL_MODEL": model_profile.counterfactual_model(),
        "DREAM_EMBED_MODEL": model_profile.embed_model(),
        "DREAM_EMBED_DIM": str(model_profile.embed_dim()),
    }


# ---------------------------------------------------------------------------
# Step 2 — claude_desktop_config.json
# ---------------------------------------------------------------------------

def inject_mcp_config(
    python_exe: str,
    dream_home: pathlib.Path,
    scripts_dir: pathlib.Path,
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
        "args": [str(scripts_dir / "mcp_server.py")],
        "env": _mcp_env(dream_home, scripts_dir),
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
    scripts_dir: pathlib.Path,
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
        "PYTHONPATH": str(scripts_dir),
    }

    def _hook(script: str, timeout: int) -> dict:
        return {
            "type": "command",
            "command": f'"{python_exe}" "{scripts_dir / script}"',
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
    scripts_dir: pathlib.Path,
    dry_run: bool,
) -> bool:
    """Create the nightly task through a .cmd wrapper.

    Two Windows constraints drive the wrapper: schtasks /TR is hard-capped at
    261 characters (interpreter + script + log paths do not fit), and the task
    must survive plugin updates (hence the deployed scripts_dir, never the
    plugin cache). /RL HIGHEST is deliberately NOT requested: it needs an
    elevated shell and made task creation fail for standard users.
    """
    scheduler_py = scripts_dir / "scheduler.py"
    wrapper = scripts_dir / "nightly.cmd"
    log_path = dream_home / "logs" / "nightly.log"
    wrapper_body = (
        "@echo off\n"
        f"set DREAM_HOME={dream_home}\n"
        f'"{python_exe}" "{scheduler_py}" --once >> "{log_path}" 2>&1\n'
    )
    cmd = [
        "schtasks", "/Create",
        "/TN", r"Dream\NightlyCycle",
        "/TR", f'"{wrapper}"',
        "/SC", "DAILY",
        "/ST", "02:05",
        "/F",               # Force-overwrite if task already exists
    ]

    if dry_run:
        print(f"  DRY-RUN: would write {wrapper} and run: {' '.join(cmd)}")
        return True

    (dream_home / "logs").mkdir(parents=True, exist_ok=True)
    wrapper.write_text(wrapper_body, encoding="ascii", errors="replace")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        print(r"  OK: Task Scheduler task 'Dream\NightlyCycle' created at 02:05 (runs nightly.cmd)")
        return True

    print(f"  WARN: schtasks exited {r.returncode}: {r.stderr.strip()}")
    print(f"  Manual command to create task:\n    {' '.join(cmd)}")
    return False


def register_backup_task(
    python_exe: str,
    dream_home: pathlib.Path,
    scripts_dir: pathlib.Path,
    dry_run: bool,
) -> bool:
    """Weekly backup of the irreplaceable PGT state (Sundays 03:00) via a .cmd
    wrapper, same constraints as the nightly task."""
    backup_py = scripts_dir / "backup.py"
    wrapper = scripts_dir / "backup.cmd"
    log_path = dream_home / "logs" / "backup.log"
    wrapper_body = (
        "@echo off\n"
        f"set DREAM_HOME={dream_home}\n"
        f'"{python_exe}" "{backup_py}" >> "{log_path}" 2>&1\n'
    )
    cmd = [
        "schtasks", "/Create",
        "/TN", r"Dream\WeeklyBackup",
        "/TR", f'"{wrapper}"',
        "/SC", "WEEKLY",
        "/D", "SUN",
        "/ST", "03:00",
        "/F",
    ]
    if dry_run:
        print(f"  DRY-RUN: would write {wrapper} and run: {' '.join(cmd)}")
        return True
    (dream_home / "logs").mkdir(parents=True, exist_ok=True)
    wrapper.write_text(wrapper_body, encoding="ascii", errors="replace")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        print(r"  OK: Task Scheduler task 'Dream\WeeklyBackup' created (Sun 03:00)")
        return True
    print(f"  WARN: schtasks exited {r.returncode}: {r.stderr.strip()}")
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

    # Step 1 — Python 3.11+
    print("[1/5] Detecting Python 3.11+ with dream deps...")
    python_exe = find_python()
    if not python_exe:
        print("  ERROR: Python 3.11+ not found.")
        print("  Install from https://www.python.org/downloads/ and ensure it is in PATH.")
        sys.exit(1)
    print(f"  Found : {python_exe}")
    if not verify_dream_deps(python_exe):
        req = SCRIPTS_DIR.parent / "requirements.txt"
        print(f"  WARN  : dream deps not installed in {python_exe}")
        print(f'  Run   : "{python_exe}" -m pip install -r "{req}"')
        print("  Continuing setup — deps can be installed after registration.")

    # Schema bootstrap
    print("\n[1b] Applying SQLite schema (db_init.py)...")
    apply_schema(python_exe, dream_home, args.dry_run)

    # Step 2 — stable script deployment
    print("\n[2/5] Deploying scripts to DREAM_HOME\\scripts (survives plugin updates)...")
    scripts_dir = deploy_scripts(dream_home, args.dry_run)

    # Step 3 — MCP config
    print("\n[3/5] Injecting mcpServers.dream into claude_desktop_config.json...")
    inject_mcp_config(python_exe, dream_home, scripts_dir, args.dry_run)

    # Step 4 — Hooks
    print("\n[4/5] Injecting SessionStart + Stop hooks into ~/.claude/settings.json...")
    inject_hooks(python_exe, dream_home, scripts_dir, args.dry_run)

    # Step 5 — Task Scheduler
    print("\n[5/6] Creating Windows Task Scheduler task 'Dream\\NightlyCycle' at 02:05...")
    register_task_scheduler(python_exe, dream_home, scripts_dir, args.dry_run)

    # Step 6 — Weekly backup
    print("\n[6/6] Creating Windows Task Scheduler task 'Dream\\WeeklyBackup' (Sun 03:00)...")
    register_backup_task(python_exe, dream_home, scripts_dir, args.dry_run)

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
