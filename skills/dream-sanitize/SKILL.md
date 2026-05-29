---
name: dream-sanitize
description: Sanitize a raw transcript or text blob locally before it enters the PGT. Use when the user asks "sanitise ce texte", "nettoie avant memo", "redact secrets", "anonymise this", "scrub before storage". Runs gemma4:e4b locally plus deterministic regex patterns. Aucun envoi reseau.
---

# Dream Sanitize

Strip secrets, credentials and personally identifying information from a text blob using a fully local pipeline.

## Instructions for Claude

1. Receive the raw content. If it is a file path, read it directly. Do not stream contents through any remote API.
2. Pass 1 - regex sweep using `references/regex-patterns.md`. Replace every match with a typed placeholder, e.g. `sk-ant-***` becomes `<SECRET:anthropic_key>`.
3. Pass 2 - call MCP tool `dream__sanitize_local` which routes to `gemma4:e4b` running on Ollama. The model receives:

```
System: You are a redaction filter. Replace personal names, emails, phone numbers,
postal addresses, financial account numbers, IP addresses, and any internal project
codename with typed placeholders. Preserve every other token, including dates and
technical jargon. Output the redacted text only, no commentary.
```

4. Pass 3 - second regex sweep to catch leakage the LLM might have introduced.
5. Hash the sanitized output (`sha256`) and compare against a deny-list of known sensitive hashes. If a match is found, refuse and ask the user to redraft.
6. Return the sanitized content with a small ledger entry `{input_sha, output_sha, model, runtime_ms}`.

## Determinism

The regex pass is deterministic and runs first. The LLM pass is non-deterministic, so it never adds tokens that were not present in the input. The post-LLM regex pass is the safety net.

## Latency budget

Target P95 `<500 ms` for a 4k token input on a 16 Go laptop.

Si la latence depasse 800 ms en P95 sur 50 echantillons consecutifs, basculer en mode `regex-only` et lever un warning Prometheus `dream_sanitize_latency_breach_total`.

## Sortie utilisateur (FR)

```
Sanitisation OK. tokens_in=1284 tokens_out=1284 remplacements=7 latence=312ms
Placeholders: <SECRET:anthropic_key>, <EMAIL>, <PERSON>
```
