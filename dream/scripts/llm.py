"""Single entry point for every model call in the stack.

Before this module the Ollama URL and the httpx client were duplicated in five
files, each with its own constant. One function replaces them, and the provider
becomes a per-role choice instead of a hardcoded one.

    reasoning roles (consolidation, counterfactual, retrieval)
        -> the Claude subscription, through the `claude -p` CLI. No API key, no
           per-token billing: the CLI reuses the local subscription auth.
    local roles (sanitise, classifier)
        -> Ollama on 127.0.0.1. Sanitisation MUST stay local: it is the pass
           that strips secrets, so its input is by definition not yet safe to
           send anywhere.

Every route is overridable:
    DREAM_LLM_PROVIDER            global default (claude | ollama)
    DREAM_PROVIDER_CONSOLIDATION  per role, same values
    DREAM_PROVIDER_COUNTERFACTUAL
    DREAM_PROVIDER_RETRIEVAL
    DREAM_PROVIDER_CLASSIFIER
    DREAM_PROVIDER_SANITISE
    DREAM_CLAUDE_MODEL            alias passed to --model (default: sonnet)
    DREAM_CLAUDE_BIN              explicit path to the claude executable
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import model_profile

OLLAMA_URL = os.environ.get("DREAM_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/generate"

_DEFAULT_PROVIDER = {
    "consolidation": "claude",
    "counterfactual": "claude",
    # Retrieval sits on the SessionStart hot path (the hook calls load_context),
    # so it stays local whatever else is configured. See _cli_allowed.
    "retrieval": "ollama",
    "classifier": "ollama",
    "sanitise": "ollama",
}

# Roles whose input is raw, unsanitised text. They may never leave the machine,
# whatever the environment says.
_LOCAL_ONLY = {"sanitise"}

_OLLAMA_MODEL = {
    "consolidation": model_profile.consolidation_model,
    "counterfactual": model_profile.counterfactual_model,
    "retrieval": model_profile.retrieval_model,
    "classifier": model_profile.classifier_model,
    "sanitise": model_profile.sanitise_model,
}


class LLMError(RuntimeError):
    pass


def cli_allowed() -> bool:
    """Spawning `claude -p` is opt-in, and only the nightly job opts in.

    The rule exists because dream runs inside the thing it would be spawning.
    The SessionStart hook fires on every Claude Desktop, Claude Code and VS Code
    session; it calls load_context, which reached the retrieval role, which
    launched a full CLI process. Claude Code cancels a hook after 10 s but does
    not kill what the hook spawned, so every session start leaked a ~300 MB
    orphan. On a 16 GB machine with ~1.3 GB free that ends one way: Claude
    Desktop and the VS Code extension die with exit code 4294967295.

    So: interactive contexts get the local model, always. `scheduler.main` sets
    DREAM_ALLOW_CLI=1 because it is the one caller that is not inside a Claude
    session and has a three-hour budget to work with.
    """
    return os.environ.get("DREAM_ALLOW_CLI", "").strip().lower() in ("1", "true", "yes")


def provider_for(role: str) -> str:
    if role in _LOCAL_ONLY:
        return "ollama"
    env = os.environ.get(f"DREAM_PROVIDER_{role.upper()}") or os.environ.get("DREAM_LLM_PROVIDER")
    choice = (env or _DEFAULT_PROVIDER.get(role, "ollama")).strip().lower()
    if choice not in ("claude", "ollama"):
        return "ollama"
    return choice if (choice != "claude" or cli_allowed()) else "ollama"


def claude_bin() -> str | None:
    """Resolve the CLI. shutil.which alone is unreliable here: under a service
    account PATHEXT may not list .CMD, which is exactly how npm ships it."""
    explicit = os.environ.get("DREAM_CLAUDE_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def available(provider: str) -> bool:
    if provider == "claude":
        return cli_allowed() and claude_bin() is not None
    try:
        import httpx

        with httpx.Client(timeout=2.0) as client:
            return client.get(OLLAMA_URL.replace("/api/generate", "/api/tags")).status_code == 200
    except Exception:
        return False


def _parse_json(text: str) -> dict[str, Any]:
    """Models fence their JSON often enough that a bare json.loads is a bug."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"no JSON object in model output: {text[:200]!r}")
        obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise LLMError(f"expected a JSON object, got {type(obj).__name__}")
    return obj


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and everything it spawned. proc.kill() only reaches the
    direct child."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=15,
            )
        else:
            proc.kill()
    except Exception:  # noqa: BLE001 - cleanup must never mask the real error
        pass
    finally:
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass


def _ask_claude(system: str, prompt: str, timeout: float) -> dict[str, Any]:
    exe = claude_bin()
    if exe is None:
        raise LLMError("claude CLI not found (set DREAM_CLAUDE_BIN)")
    argv = [
        exe,
        "-p",
        "--output-format", "json",
        "--model", os.environ.get("DREAM_CLAUDE_MODEL", "sonnet"),
        "--system-prompt", system,
        # This is an inference call, not an agent session. --tools "" removes
        # the tool loop; --safe-mode removes CLAUDE.md, plugins, MCP servers and
        # hooks while leaving subscription auth intact. Without it the CLI runs
        # dream's own SessionStart hook on every call: the stack invoking itself,
        # ~10 s of startup per role, four roles per cluster.
        "--tools", "",
        "--safe-mode",
        "--no-session-persistence",
    ]
    if exe.lower().endswith((".cmd", ".bat")):
        # CreateProcess quoting for .cmd is a known trap; go through cmd.exe.
        argv = ["cmd", "/c"] + argv

    # Popen, not run(): the child is cmd.exe and the CLI is its grandchild, so a
    # timeout that only kills the child leaves the CLI running. Twenty of those
    # orphans held 2.9 GB and starved the machine into killing the cycle. Kill
    # the whole tree.
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))),
    )
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_tree(proc)
        raise LLMError(f"claude CLI timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise LLMError(f"claude CLI exited {proc.returncode}: {(stderr or '')[:300]}")
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LLMError(f"claude CLI returned non-JSON: {stdout[:200]!r}") from exc
    if envelope.get("is_error"):
        raise LLMError(f"claude CLI reported an error: {str(envelope.get('result'))[:300]}")
    return _parse_json(envelope.get("result", ""))


def _ask_ollama(role: str, system: str, prompt: str, timeout: float, options: dict | None) -> dict[str, Any]:
    import httpx

    payload = {
        "model": _OLLAMA_MODEL[role](),
        "system": system,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": options or {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        return _parse_json(resp.json().get("response", ""))


def ask(
    role: str,
    system: str,
    prompt: str,
    *,
    timeout: float = 120.0,
    options: dict | None = None,
    fallback: bool = True,
) -> dict[str, Any]:
    """Ask the model bound to `role` and return a parsed JSON object.

    Raises LLMError if every allowed provider fails. Callers that must never
    break the cycle catch it and degrade, they always did.
    """
    primary = provider_for(role)
    order = [primary]
    if fallback and role not in _LOCAL_ONLY:
        other = "ollama" if primary == "claude" else "claude"
        if other != "claude" or cli_allowed():
            order.append(other)

    errors: list[str] = []
    for provider in order:
        try:
            if provider == "claude":
                return _ask_claude(system, prompt, timeout)
            return _ask_ollama(role, system, prompt, timeout, options)
        except Exception as exc:  # noqa: BLE001 - the next provider is the handler
            errors.append(f"{provider}: {exc}")
    raise LLMError("; ".join(errors))


def demo() -> None:
    """Self-check: routing, local-only pinning, and lenient JSON parsing."""
    was = os.environ.pop("DREAM_ALLOW_CLI", None)

    assert provider_for("sanitise") == "ollama"
    os.environ["DREAM_PROVIDER_SANITISE"] = "claude"
    assert provider_for("sanitise") == "ollama", "sanitisation must stay local"
    del os.environ["DREAM_PROVIDER_SANITISE"]

    # Without the opt-in nothing may spawn a CLI, not even an explicit override.
    assert provider_for("consolidation") == "ollama"
    os.environ["DREAM_LLM_PROVIDER"] = "claude"
    assert provider_for("retrieval") == "ollama", "no subprocess without the opt-in"
    del os.environ["DREAM_LLM_PROVIDER"]

    os.environ["DREAM_ALLOW_CLI"] = "1"
    assert provider_for("consolidation") == "claude"
    assert provider_for("retrieval") == "ollama", "retrieval stays local: hook hot path"
    os.environ["DREAM_LLM_PROVIDER"] = "ollama"
    assert provider_for("consolidation") == "ollama"
    del os.environ["DREAM_LLM_PROVIDER"]
    if was is None:
        del os.environ["DREAM_ALLOW_CLI"]
    else:
        os.environ["DREAM_ALLOW_CLI"] = was

    assert _parse_json('{"a": 1}') == {"a": 1}
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('Voici: {"a": 1} voila') == {"a": 1}
    try:
        _parse_json("pas de json")
    except LLMError:
        pass
    else:
        raise AssertionError("expected LLMError")
    print("llm.py demo ok")


if __name__ == "__main__":
    demo()
