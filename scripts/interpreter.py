"""Resolve a Python interpreter that has the dream deps installed.

stdlib-only. Used by the hook scripts to re-exec themselves under a
deps-capable interpreter when the plugin runtime launches them with a bare
`python` that resolves to the wrong version (a recurring Windows failure mode,
where `python` may be a 3.14 install without the dream stack). This makes the
plugin-manager hook path and the manual setup path behave identically.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Representative marker: if sentence-transformers is importable, the whole
# dream stack was installed (both setup scripts probe it together).
_MARKER = "sentence_transformers"
_PROBE = "import lancedb, mcp, sentence_transformers, networkx, cryptography, apscheduler"


def deps_present() -> bool:
    """True if the current interpreter can import the dream stack marker."""
    return importlib.util.find_spec(_MARKER) is not None


def _probe(exe: str) -> bool:
    try:
        r = subprocess.run([exe, "-c", _PROBE], capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def find_python_with_deps() -> str | None:
    """Return a Python executable with the dream deps, or None."""
    if deps_present():
        return sys.executable

    candidates: list[str] = []
    if os.name == "nt":
        for flag in ("-3.12", "-3.12-64", "-3.12-32"):
            try:
                r = subprocess.run(
                    ["py", flag, "-c", "import sys; print(sys.executable)"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    candidates.append(r.stdout.strip())
            except FileNotFoundError:
                break
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.append(str(Path(local) / "Programs/Python/Python312/python.exe"))
        candidates.append("C:/Python312/python.exe")
    else:
        for name in ("python3", "python"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for exe in candidates:
        if exe and Path(exe).exists() and _probe(exe):
            return exe
    return None


def reexec_if_needed(script_file: str) -> None:
    """Re-exec `script_file` under a deps-capable interpreter when the current
    one lacks the stack. Guarded by DREAM_REEXEC to avoid loops. No-op when the
    current interpreter already has the deps."""
    if os.environ.get("DREAM_REEXEC") == "1" or deps_present():
        return
    exe = find_python_with_deps()
    if not exe or os.path.realpath(exe) == os.path.realpath(sys.executable):
        return
    os.environ["DREAM_REEXEC"] = "1"
    os.execv(exe, [exe, os.path.abspath(script_file), *sys.argv[1:]])
