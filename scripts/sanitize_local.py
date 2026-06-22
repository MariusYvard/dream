"""Two-pass local sanitisation: deterministic regex then gemma4:e4b via Ollama.

Public API:
    sanitize(text: str) -> tuple[str, dict]

The dict reports replacements, the model used and the runtime in ms.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Iterable

import httpx

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
SANITISE_MODEL = "gemma4:e4b"

# Order matters: most-specific vendor prefixes first.
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{40,}"), "<SECRET:anthropic_key>"),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{32,}"), "<SECRET:openai_key>"),
    ("github_pat", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"), "<SECRET:github_pat>"),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}"), "<SECRET:aws_key>"),
    ("stripe_key", re.compile(r"(sk_live|pk_live|rk_live)_[A-Za-z0-9]{24,}"), "<SECRET:stripe_key>"),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"), "<SECRET:private_key>"),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"), "<SECRET:slack_webhook>"),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "<SECRET:jwt>"),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-.]{20,}"), "<SECRET:bearer>"),
    ("api_key_generic", re.compile(r"(?i)(api[_-]?key|apikey)['\"\s:=]+[A-Za-z0-9_\-]{24,}"), "<SECRET:api_key>"),
    ("env_path", re.compile(r"[\w./\-]*\.env(?:\.[\w\-]+)?"), "<ENV_PATH>"),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b"), "<IBAN>"),
    ("card", re.compile(r"\b(?:\d[ \-]*?){13,16}\b"), "<CARD>"),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<IP>"),
    ("phone_intl", re.compile(r"\+\d{1,3}[\s.\-]?\d{1,4}[\s.\-]?\d{2,4}[\s.\-]?\d{2,4}[\s.\-]?\d{0,4}"), "<PHONE>"),
    ("phone_fr", re.compile(r"\b0[1-9](?:[\s.\-]?\d{2}){4}\b"), "<PHONE>"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
]

LLM_SYSTEM_PROMPT = (
    "You are a redaction filter. Replace personal names, postal addresses, financial account "
    "numbers and internal codenames with typed placeholders (<PERSON>, <ADDRESS>, <ACCOUNT>, "
    "<CODENAME>). Preserve every other token, including dates and technical jargon. Output "
    "the redacted text only, no commentary."
)


@dataclass
class SanitiseResult:
    text: str
    replacements: dict[str, int]
    model: str
    runtime_ms: int
    input_sha: str
    output_sha: str


def _regex_pass(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for name, pattern, placeholder in PATTERNS:
        new_text, n = pattern.subn(placeholder, text)
        if n:
            counts[name] = counts.get(name, 0) + n
        text = new_text
    return text, counts


def _llm_pass(text: str) -> str:
    payload = {
        "model": SANITISE_MODEL,
        "system": LLM_SYSTEM_PROMPT,
        "prompt": text,
        "stream": False,
        "options": {"temperature": 0.0, "top_p": 0.95, "top_k": 64, "num_predict": 4096},
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        return resp.json().get("response", text).strip()


def _merge_counts(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def sanitize_regex_only(text: str) -> SanitiseResult:
    """Deterministic regex-only redaction. No LLM, no network, fast.

    Used on the Stop hook critical path so session exit never blocks on the
    ~30 s local model. The nightly cycle upgrades the content with the full LLM
    pass before it reaches topics or the graph.
    """
    start = time.perf_counter()
    input_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    final, counts = _regex_pass(text)
    runtime_ms = int((time.perf_counter() - start) * 1000)
    return SanitiseResult(
        text=final,
        replacements=counts,
        model="regex-only",
        runtime_ms=runtime_ms,
        input_sha=input_sha,
        output_sha=hashlib.sha256(final.encode("utf-8")).hexdigest(),
    )


def sanitize(text: str) -> SanitiseResult:
    start = time.perf_counter()
    input_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    stage1, counts1 = _regex_pass(text)
    try:
        stage2 = _llm_pass(stage1)
    except Exception:
        # Local model offline: stay strict, regex-only path.
        stage2 = stage1
    final, counts2 = _regex_pass(stage2)

    runtime_ms = int((time.perf_counter() - start) * 1000)
    output_sha = hashlib.sha256(final.encode("utf-8")).hexdigest()
    return SanitiseResult(
        text=final,
        replacements=_merge_counts(counts1, counts2),
        model=SANITISE_MODEL,
        runtime_ms=runtime_ms,
        input_sha=input_sha,
        output_sha=output_sha,
    )


if __name__ == "__main__":
    import sys

    payload = sys.stdin.read()
    result = sanitize(payload)
    print(
        json.dumps(
            {
                "text": result.text,
                "replacements": result.replacements,
                "model": result.model,
                "runtime_ms": result.runtime_ms,
                "input_sha": result.input_sha,
                "output_sha": result.output_sha,
            },
            ensure_ascii=False,
        )
    )
