"""Smoke test for mcp_server.py — verifies the 10 tools are registered.

Runs `mcp_server.py --smoke`, which lists tool names via the public FastMCP API
(`mcp.list_tools()`). Heavy modules (sentence-transformers, lancedb, numpy,
networkx) are imported lazily inside the tools, so --smoke does NOT load the ML
stack and does NOT start the server or call Ollama. It needs only the light
deps (mcp, cryptography, prometheus-client, httpx).
"""
import subprocess
import sys
import json
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def test_all_tools_registered():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "mcp_server.py"), "--smoke"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(SCRIPTS_DIR),
    )
    assert result.returncode == 0, f"smoke exited {result.returncode}: {result.stderr}"

    data = json.loads(result.stdout)
    tools = set(data["tools"])

    expected = {
        "store_event",
        "search_semantic",
        "query_relations",
        "update_vitality",
        "propose_counterfactual",
        "sanitize_local",
        "load_context",
        "health_check",
        "set_mode",
        "verify_counterfactual",
    }
    missing = expected - tools
    assert not missing, f"Missing tools: {missing}"
