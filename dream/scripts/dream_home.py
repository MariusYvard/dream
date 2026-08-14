"""Resolve the live DREAM_HOME the same way every entry point does.

Skills and ad-hoc scripts used to assume the source repo, while the running
stack lives in the deployed DREAM_HOME. This helper makes them agree:

    1. $DREAM_HOME if set
    2. mcpServers.dream.env.DREAM_HOME in claude_desktop_config.json
    3. ~/.dream

    python dream_home.py   # prints the resolved absolute path
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _from_desktop_config() -> str | None:
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    candidates.append(Path.home() / "Library/Application Support/Claude/claude_desktop_config.json")
    candidates.append(Path.home() / ".config/Claude/claude_desktop_config.json")
    for cfg in candidates:
        try:
            if cfg.exists():
                data = json.loads(cfg.read_text(encoding="utf-8"))
                home = (((data.get("mcpServers") or {}).get("dream") or {}).get("env") or {}).get("DREAM_HOME")
                if home:
                    return home
        except Exception:
            continue
    return None


def resolve() -> Path:
    env = os.environ.get("DREAM_HOME")
    if env:
        return Path(env)
    cfg = _from_desktop_config()
    if cfg:
        return Path(cfg)
    return Path.home() / ".dream"


if __name__ == "__main__":
    sys.stdout.write(str(resolve()))
