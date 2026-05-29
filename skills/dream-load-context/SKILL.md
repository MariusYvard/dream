---
name: dream-load-context
description: Assemble a token-efficient working context from the PGT for the current session. Use when the user asks "charge mon contexte", "prepare ma session", "load dream context", "donne moi le rappel du projet", "qu'est-ce que je faisais hier", or automatically at SessionStart. Produces a <2k token bundle from the highest vitality nodes aligned with the active goal.
---

# Dream Load Context

Produce a session warm-up bundle by pulling the most relevant high-vitality nodes from the PGT.

## Instructions for Claude

1. Detect the active goal. Order of resolution:
   - explicit user statement ("je travaille sur X"),
   - last user message in this session,
   - `${DREAM_HOME}/active_goal.txt` (set by the previous Stop hook),
   - default: latest `type=decision` node with `vitality > 0.7`.

2. Embed the active goal once via `bge-m3`. Cache the vector for the rest of the session.

3. Call `dream__load_context` with:

```json
{
  "goal_vector": [...],
  "token_budget": 2000,
  "vitality_min": 0.5,
  "topics_include": null,
  "topics_exclude": ["chit-chat"]
}
```

4. The server returns a structured bundle:

```json
{
  "claude_md": "<short index, <500 tokens>",
  "topics": [
    {"name": "pgt-architecture", "content": "...", "tokens": 380},
    {"name": "outils-cron", "content": "...", "tokens": 210}
  ],
  "recent_decisions": [{"id": "...", "summary": "..."}],
  "pending_hitl": [{"id": "...", "summary": "..."}],
  "total_tokens": 1850
}
```

5. Inject the bundle into the working context. Never exceed `token_budget`. If the bundle returns over budget, trim from the lowest vitality topic first.

6. Surface to the user a one-line confirmation, never the bundle itself unless asked:

```
Contexte charge: 6 topics, 4 decisions recentes, 2 HITL en attente. 1850 tokens.
```

## Idempotence

If the SessionStart hook already loaded the bundle within the last 5 minutes, reuse the cached version. Cache key: `sha256(goal_vector || token_budget)`.

## Refus

Refuser si le budget de tokens est inferieur a 256 (information utile devient impossible).

Refuser si plus de 5 noeuds HITL sont en attente: forcer l'utilisateur a traiter les arbitrages avant de reouvrir une session normale.
