"""Feed the buffer by reading session transcripts off disk.

The Stop hook was the only automatic write path, and under Cowork it does not
fire (and under the CLI it has been seen to hit its own timeout and be
cancelled). The buffer then sits empty for weeks and the nightly cycle has
nothing to consolidate, which is the single most reported failure of this
stack. Transcripts are on disk either way, so read those instead: no hook, no
timeout budget, and a missed session is picked up on the next run.

State is a per-file byte offset, so a 28 MB transcript is read once and then
only its tail.

    DREAM_TRANSCRIPT_ROOTS  os.pathsep-separated override of the search roots
    DREAM_SCAN_MAX_EVENTS   per-run cap (default 800)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

import dream_buffer
import load_bearing

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
STATE_PATH = DREAM_HOME / "logs" / "session_scan.json"
MAX_EVENTS = int(os.environ.get("DREAM_SCAN_MAX_EVENTS", "800"))
_MAX_CONTENT = 4000

# Blocks the harness injects into the user turn. They look like user prose to a
# naive reader and are the bulk of what a first scan picks up, but they are
# plumbing, not something the person said or decided.
_INJECTED_PREFIXES = (
    "<task-notification>", "<system-reminder>", "<command-message>",
    "<command-name>", "<local-command-", "<user-prompt-submit-hook>",
    "Caveat: The messages below", "<bash-input>", "<bash-stdout>",
)


def roots() -> list[Path]:
    override = os.environ.get("DREAM_TRANSCRIPT_ROOTS")
    if override:
        return [Path(p) for p in override.split(os.pathsep) if p.strip()]
    out = [Path.home() / ".claude" / "projects"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "Claude" / "local-agent-mode-sessions")
    return [p for p in out if p.exists()]


def transcripts() -> list[Path]:
    """Session transcripts only: subagent logs duplicate their parent's
    reasoning and audit.jsonl is telemetry, neither is memory."""
    found: list[Path] = []
    for root in roots():
        for path in root.rglob("*.jsonl"):
            if "subagents" in path.parts or path.name == "audit.jsonl":
                continue
            found.append(path)
    return sorted(found)


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def extract_text(entry: dict[str, Any]) -> str:
    """Pull the prose out of one transcript line, or "" if it carries none."""
    if entry.get("type") not in ("user", "assistant") or entry.get("isSidechain"):
        return ""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p).strip()
    else:
        return ""
    return "" if text.startswith(_INJECTED_PREFIXES) else text


def _project_of(entry: dict[str, Any]) -> str | None:
    cwd = entry.get("cwd")
    return Path(cwd).name if cwd else None


def _new_lines(path: Path, offset: int) -> Iterator[tuple[int, str]]:
    """Yield (offset_after_line, line) from `offset` on. Byte offsets, because
    a transcript is appended to while we read it."""
    with path.open("rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(0 if offset > size else offset)  # truncated or rotated: restart
        for raw in fh:
            yield fh.tell(), raw.decode("utf-8", errors="replace")


def scan(*, dry_run: bool = False) -> dict[str, Any]:
    state = _load_state()
    seen: set[str] = set()
    ingested = 0
    files_touched = 0

    for path in transcripts():
        key = str(path)
        offset = int(state.get(key, {}).get("offset", 0))
        last_offset = offset
        before = ingested

        for new_offset, line in _new_lines(path, offset):
            if ingested >= MAX_EVENTS:
                break  # keep the offset where it is; the next run resumes here
            last_offset = new_offset
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = extract_text(entry)
            if not text or not load_bearing.is_candidate(text):
                continue
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            if not dry_run:
                dream_buffer.append_event(
                    {
                        "type": "fact",
                        "content": text[:_MAX_CONTENT],
                        "project": _project_of(entry),
                        "meta": {
                            "source_session": entry.get("sessionId"),
                            "source": "session_scan",
                            "observed_at": entry.get("timestamp"),
                        },
                    },
                    full_llm=False,  # the cycle upgrades the sanitisation
                )
            ingested += 1

        if last_offset != offset:
            files_touched += 1
            state[key] = {"offset": last_offset, "seen_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        if ingested >= MAX_EVENTS:
            break

    if not dry_run:
        _save_state(state)
    return {"files": files_touched, "events": ingested, "capped": ingested >= MAX_EVENTS}


def demo() -> None:
    """Self-check on a synthetic transcript: extraction, resume, dedupe."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="dream_scan_"))
    os.environ["DREAM_TRANSCRIPT_ROOTS"] = str(tmp)
    global STATE_PATH
    STATE_PATH = tmp / "state.json"

    rule = "Regle: toujours ecrire via bash, l'outil Write tronque les fichiers sur OneDrive."
    lines = [
        {"type": "queue-operation", "content": "bruit"},
        {"type": "user", "message": {"role": "user", "content": rule}, "cwd": "C:\\proj\\demo"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
        {"type": "user", "isSidechain": True, "message": {"content": rule}},
    ]
    target = tmp / "session.jsonl"
    target.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")

    assert extract_text(lines[1]) == rule
    assert extract_text(lines[0]) == ""
    assert extract_text(lines[3]) == "", "sidechain lines are not memory"

    first = scan(dry_run=True)
    assert first["events"] == 1, first  # only the rule is load-bearing

    _save_state({str(target): {"offset": target.stat().st_size}})
    assert scan(dry_run=True)["events"] == 0, "an unchanged transcript must be re-read for nothing"

    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": rule}}) + "\n")
    assert scan(dry_run=True)["events"] == 1, "the appended tail must be picked up"

    print("session_scan.py demo ok")


if __name__ == "__main__":
    import sys

    if "--demo" in sys.argv:
        demo()
    else:
        print(json.dumps(scan(dry_run="--dry-run" in sys.argv), ensure_ascii=False))
