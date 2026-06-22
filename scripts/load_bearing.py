"""Load-bearing classification for buffer events.

The Stop hook keeps a cheap lexical pre-filter (recall-oriented, LLM-free so it
never blocks session exit). The real decision runs here during the nightly
cycle, where there is time for a local-LLM judgment that is robust and
language-agnostic instead of a hardcoded token list. A lexical fast-path
short-circuits the obvious cases, a length floor drops chit-chat, and verdicts
are cached by content hash so re-runs never re-pay the model.
"""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from pathlib import Path

import httpx

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
CLASSIFIER_MODEL = os.environ.get("DREAM_CLASSIFIER_MODEL", "gemma4:e4b")
DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
_CACHE_PATH = DREAM_HOME / "logs" / "load_bearing_cache.json"
_MIN_CHARS = 24

# Cheap recall-oriented lexical hints (accent-folded), used as the hook
# pre-filter and as the cycle fast-path.
HINT_TOKENS: tuple[str, ...] = (
    "decide", "decision", "regle", "convention", "il faut", "ne plus", "ne pas",
    "toujours", "jamais", "correction", "error", "erreur", "bug", "retry", "fix",
    "architecture", "refactor", "migration", "deprec", "procedure", "workflow",
    "process", "objectif", "priorite", "deadline", "livrable",
)

LLM_SYSTEM = (
    "You decide whether a single conversation line carries durable, load-bearing "
    "memory: a decision, a correction, a rule or convention, an architectural or "
    "process fact, a commitment or a deadline. Chit-chat, acknowledgements and "
    "transient status are NOT load-bearing. Answer with strict JSON "
    '{"load_bearing": true|false}. No commentary.'
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii").lower()


def lexical_hit(text: str) -> bool:
    folded = _fold(text)
    return any(tok in folded for tok in HINT_TOKENS)


def is_candidate(text: str) -> bool:
    """Cheap, LLM-free pre-filter for the Stop hook (recall over precision)."""
    return len((text or "").strip()) >= _MIN_CHARS and lexical_hit(text)


def _load_cache() -> dict:
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if len(cache) > 2000:
            cache = dict(list(cache.items())[-2000:])
        _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _ask_llm(text: str) -> bool | None:
    payload = {
        "model": CLASSIFIER_MODEL,
        "system": LLM_SYSTEM,
        "prompt": text[:2000],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_predict": 32},
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            data = json.loads(resp.json().get("response", "{}"))
        return bool(data.get("load_bearing", False))
    except Exception:
        return None


def classify(text: str, *, use_llm: bool = True) -> bool:
    """Decide if a line is load-bearing. LLM with lexical fast-path + cache.

    Degrades to the lexical decision when the local model is unreachable, so the
    cycle never loses events because Ollama hiccuped.
    """
    t = (text or "").strip()
    if len(t) < _MIN_CHARS:
        return False
    if not use_llm:
        return lexical_hit(t)

    key = hashlib.sha256(t.encode("utf-8")).hexdigest()
    cache = _load_cache()
    if key in cache:
        return bool(cache[key])

    verdict = _ask_llm(t)
    if verdict is None:
        return lexical_hit(t)  # degrade, do not cache an Ollama-down fallback
    cache[key] = verdict
    _save_cache(cache)
    return verdict
