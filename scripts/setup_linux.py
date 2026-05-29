"""
setup_linux.py — Register Dream on Linux (systemd user session).

stdlib-only: safe to run before dream deps are installed.

Actions
-------
1. Detect the Python 3 executable that has dream deps installed.
2. Inject mcpServers.dream into ~/.config/claude/claude_desktop_config.json
   (Claude Desktop for Linux) or warn if absent.
3. Inject SessionStart + Stop hooks into ~/.claude/settings.json.
4. Install systemd user unit files and enable the timer.

Usage
-----
    python setup_linux.py [--dream-home PATH] [--dry-run]

--dream-home   defaults to $DREAM_HOME or ~/.dream
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


# ---------------------------------------------------------------------------
# Python detection
# ---------------------------------------------------------------------------

def find_python() -> Optional[str]:
    """Return the Python executable that has the dream deps, or None."""
    for candidate in ("python3", "python"):
        exe = shutil.which(candidate)
        if not exe:
            continue
        probe = "import lancedb, mcp, sentence_transformers, networkx, cryptography, apscheduler"
        r = subprocess.run([exe, "-c", probe], capture_output=True, timeout=20)
        if r.returncode == 0:
            return exe
    return None


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
# Step 2 — MCP config
# ---------------------------------------------------------------------------

def inject_mcp_config(python_exe: str, dream_home: pathlib.Path, dry_run: bool) -> bool:
    # Claude Desktop for Linux uses XDG_CONFIG_HOME
    xdg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
    config_path = xdg / "claude" / "claude_desktop_config.json"

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

    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  OK: mcpServers.dream written to {config_path}")
    return True


# ---------------------------------------------------------------------------
# Step 3 — ~/.claude/settings.json hooks
# ---------------------------------------------------------------------------

def inject_hooks(python_exe: str, dream_home: pathlib.Path, dry_run: bool) -> bool:
    settings_path = pathlib.Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  WARN: could not parse existing settings ({exc}), starting fresh")

    env = {"DREAM_HOME": str(dream_home), "PYTHONPATH": str(dream_home / "scripts")}

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

    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  OK: SessionStart + Stop hooks written to {settings_path}")
    return True


# ---------------------------------------------------------------------------
# Step 4 — systemd user timer
# ---------------------------------------------------------------------------

def install_systemd_units(python_exe: str, dream_home: pathlib.Path, plugin_root: pathlib.Path, dry_run: bool) -> bool:
    unit_dir = pathlib.Path.home() / ".config" / "systemd" / "user"
    service_src = plugin_root / "systemd" / "dream-cycle.service"
    timer_src = plugin_root / "systemd" / "dream-cycle.timer"

    if not service_src.exists() or not timer_src.exists():
        print(f"  ERROR: systemd unit files not found under {plugin_root / 'systemd'}")
        print("  Expected: dream-cycle.service and dream-cycle.timer")
        return False

    # Rewrite ExecStart with the resolved python path and dream_home.
    service_text = service_src.read_text(encoding="utf-8")
    service_text = service_text.replace(
        "/usr/bin/env python3 %h/.dream/scripts/scheduler.py --once",
        f'"{python_exe}" "{dream_home / "scripts" / "scheduler.py"}" --once',
    )
    service_text = service_text.replace(
        "Environment=DREAM_HOME=%h/.dream",
        f"Environment=DREAM_HOME={dream_home}",
    ).replace(
        "Environment=PYTHONPATH=%h/.dream/scripts",
        f"Environment=PYTHONPATH={dream_home / 'scripts'}",
    )

    if dry_run:
        print(f"  DRY-RUN: would install units to {unit_dir}")
        print(f"  DRY-RUN: would run: systemctl --user daemon-reload")
        print(f"  DRY-RUN: would run: systemctl --user enable --now dream-cycle.timer")
        return True

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "dream-cycle.service").write_text(service_text, encoding="utf-8")
    shutil.copy(timer_src, unit_dir / "dream-cycle.timer")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "dream-cycle.timer"], check=True)
    print(f"  OK: systemd timer 'dream-cycle.timer' enabled at 02:05")
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    plugin_root = pathlib.Path(__file__).parent.parent

    parser = argparse.ArgumentParser(
        description="Register the Dream plugin on Linux (MCP config, hooks, systemd timer)."
    )
    parser.add_argument(
        "--dream-home",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("DREAM_HOME", pathlib.Path.home() / ".dream")),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dream_home: pathlib.Path = args.dream_home

    print("=" * 55)
    print("  Dream Linux Registration")
    print("=" * 55)
    print(f"  DREAM_HOME : {dream_home}")
    print(f"  dry-run    : {args.dry_run}")
    print()

    print("[1/4] Detecting Python with dream deps...")
    python_exe = find_python()
    if not python_exe:
        print("  ERROR: No Python with dream deps found.")
        print(f"  Run: pip install -r {plugin_root / 'requirements.txt'}")
        sys.exit(1)
    print(f"  Found : {python_exe}")

    print("\n[1b] Applying SQLite schema (db_init.py)...")
    apply_schema(python_exe, dream_home, args.dry_run)

    print("\n[2/4] Injecting mcpServers.dream into claude_desktop_config.json...")
    inject_mcp_config(python_exe, dream_home, args.dry_run)

    print("\n[3/4] Injecting SessionStart + Stop hooks...")
    inject_hooks(python_exe, dream_home, args.dry_run)

    print("\n[4/4] Installing systemd user units...")
    install_systemd_units(python_exe, dream_home, plugin_root, args.dry_run)

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
