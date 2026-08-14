"""Hierarchical topic tree over the consolidated topics/*.md files.

PageIndex-style: a table-of-contents tree of summaries that an LLM can reason
over, instead of a flat per-type append log. The tree is derived
deterministically from topics/*.md (no model call to build it), persisted to
topics/_tree.json each cycle, and consumed by load_context for vectorless,
reasoning-based, traceable retrieval.

Shape:
    {
      "title": "Dream Memory",
      "updated_at": "<iso>",
      "children": [
        {"node_id": "decision", "title": "decision", "summary": "...",
         "children": [
            {"node_id": "decision-0", "title": "...", "summary": "...", "ts": "<iso>"}
         ]}
      ]
    }
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
TOPICS_DIR = DREAM_HOME / "topics"
TREE_PATH = TOPICS_DIR / "_tree.json"

# A topic line written by scheduler._write_topic: "- {iso} :: {summary}".
_LINE = re.compile(r"^-\s+(?P<ts>\S+)\s+::\s+(?P<summary>.+)$")
_TITLE_CHARS = 60


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _entries(md_path: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    try:
        for line in md_path.read_text(encoding="utf-8").splitlines():
            m = _LINE.match(line.strip())
            if m:
                out.append({"ts": m.group("ts"), "summary": m.group("summary").strip()})
    except Exception:
        pass
    return out


def build_tree() -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    if TOPICS_DIR.exists():
        for md in sorted(TOPICS_DIR.glob("*.md")):
            if md.name.startswith("_"):
                continue
            topic = md.stem
            entries = _entries(md)
            if not entries:
                continue
            leaves = [
                {
                    "node_id": f"{topic}-{i}",
                    "title": e["summary"][:_TITLE_CHARS],
                    "summary": e["summary"],
                    "ts": e["ts"],
                }
                for i, e in enumerate(entries)
            ]
            recent = " | ".join(leaf["title"] for leaf in leaves[-3:])
            children.append({
                "node_id": topic,
                "title": topic,
                "summary": f"{len(leaves)} entries. Recent: {recent}",
                "children": leaves,
            })
    return {"title": "Dream Memory", "updated_at": _now(), "children": children}


def build_and_save() -> dict[str, Any]:
    tree = build_tree()
    try:
        TOPICS_DIR.mkdir(parents=True, exist_ok=True)
        TREE_PATH.write_text(json.dumps(tree, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return tree


def load_tree() -> dict[str, Any]:
    try:
        if TREE_PATH.exists():
            return json.loads(TREE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return build_tree()


def render_for_llm(tree: dict[str, Any], max_chars: int = 4000) -> str:
    """Compact outline (node_id, title, summary) small enough to reason over."""
    lines: list[str] = []
    for tnode in tree.get("children", []):
        lines.append(f"[{tnode['node_id']}] {tnode['title']}: {tnode.get('summary', '')}")
        for leaf in tnode.get("children", []):
            lines.append(f"  [{leaf['node_id']}] {leaf['summary']}")
    return "\n".join(lines)[:max_chars]


def collect(tree: dict[str, Any], node_ids: list[str]) -> list[dict[str, str]]:
    """Return the entries whose node_id is selected. Selecting a type node pulls
    all its leaves. Order preserved, deduplicated."""
    want = set(node_ids)
    out: list[dict[str, str]] = []
    for tnode in tree.get("children", []):
        type_selected = tnode["node_id"] in want
        for leaf in tnode.get("children", []):
            if type_selected or leaf["node_id"] in want:
                out.append({"node_id": leaf["node_id"], "title": leaf["title"], "summary": leaf["summary"]})
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for e in out:
        if e["node_id"] not in seen:
            seen.add(e["node_id"])
            uniq.append(e)
    return uniq


def render_index_md(tree: dict[str, Any], max_tokens: int = 500) -> str:
    """Hierarchical markdown index (a table of contents) for CLAUDE.md."""
    lines = ["# Dream Index", tree.get("updated_at", _now()), ""]
    for tnode in tree.get("children", []):
        lines.append(f"## {tnode['title']}")
        for leaf in tnode.get("children", [])[-8:]:
            lines.append(f"- {leaf['summary'][:120]}")
        lines.append("")
    text = "\n".join(lines)
    if len(text) // 4 > max_tokens:
        text = text[: max_tokens * 4]
    return text
