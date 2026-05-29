---
name: dream-store-event
description: Persist a sanitized event (fact, decision, code snippet, person, process) into the Palais Graphique Temporel. Use when the user asks "stocke ce fait", "memorise cette decision", "garde en memoire", "enregistre dans le PGT", "log this in dream", "remember that...". Routes through local sanitisation then writes a node + edges with full temporal bounds and a fresh vitality score.
---

# Dream Store Event

Capture a single event and commit it to the PGT 2.0 graph + LanceDB index. All writes pass the sanitisation gate first.

## Instructions for Claude

1. Build the candidate payload according to the node schema:

```json
{
  "id": "<uuid4>",
  "type": "fact | decision | code_snippet | person | process | error",
  "content": "<raw user content>",
  "validity": {"from": "<ISO8601 now>", "to": null, "confidence": 0.85},
  "meta": {"source_session": "<session_id>", "consensus_score": null}
}
```

2. Resolve every relative date in `content` to ISO 8601 UTC before storing. "Hier" becomes the explicit date.
3. Call the MCP tool `dream__store_event` with the payload. The server pipes the content through `sanitize_local.py` (gemma4:e4b + regex) and refuses any text containing `sk-ant-*`, `ghp_*`, `AKIA*` or paths to `.env`.
4. If the server returns `status: rejected_secret`, inform the user, redact the offending span and propose a clean rewrite. Never bypass the gate.
5. If `relation_hints` are provided (parent node id, relation type), pass them through so the server materialises the edges with `weight=0.8`, `relation_type` in `{implements, depends_on, contradicts, supersedes}` and the active temporal bounds.
6. On success, the server returns the new node id, the assigned vitality `V(t=0)=0.9` and the ledger entry hash. Surface these three values to the user, nothing more.

## Edge creation policy

When the new node `contradicts` an existing one (semantic similarity >0.82 and explicit negation token), create a `contradicts` edge and demote the old node's vitality by `delta=0.10` immediately, do not wait for the nightly job.

When the new node `supersedes` an older one (same subject + newer timestamp + same `type`), mark the old node's `validity.to` to the new node's `validity.from` and add a `supersedes` edge.

## Refus

Refuser le stockage si:
- l'evenement contient une mention explicite "ne pas memoriser",
- la confidence reportee par l'utilisateur est <0.3,
- le `type` est absent ou hors enumeration.

## Sortie utilisateur (FR)

Format minimal a renvoyer au user:

```
Stocke. id=<uuid> vitalite=0.90 ledger=<sha256[:12]>
```

Aucun commentaire additionnel sauf si l'utilisateur le demande.
