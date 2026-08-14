---
name: dream-counterfactual
description: Generate counterfactual scenarios from an error or low-consensus node and grow the Counterfactual Garden. Use when the user asks "et si on avait fait autrement", "explore l'alternative", "que se serait-il passe si", "what if", "counterfactual on", "jardin contrefactuel", or when triggered automatically on a node tagged type=error or consensus_score<0.4. Produces 2-3 candidate branches stored as a read-only sub-graph.
---

# Dream Counterfactual

Grow a Counterfactual Garden (CER/DCER) on a seed node. The branches stay in a read-only sub-graph until reality validates one of them.

## Instructions for Claude

1. Identify the seed node. Eligible seeds:
   - `type=error`,
   - `consensus_score < 0.4`,
   - explicit user request with a target node id.

2. Generate 2 to 3 alternative branches via the local model `gemma4:26b` (MoE, 3.8B params actifs). The prompt template (see `references/cer-dcer-protocol.md`) forces JSON output:

```json
{
  "branches": [
    {
      "action_alt": "<what could have been done instead>",
      "predicted_outcome": "<expected consequence>",
      "preconditions": ["<list>"],
      "horizon_days": 7
    }
  ]
}
```

3. Evaluate every branch with the Critique agent:
   - `risk_score` (0..1, higher is safer),
   - `coherence` with the existing graph,
   - `alignment_with_goals` (cosine with `active_goals_embedding`).
   - Final: `branch_quality = 0.4*risk + 0.3*coherence + 0.3*alignment`.

4. Store accepted branches (`branch_quality >= 0.55`) as nodes with:
   - `scenario = "counterfactual"`,
   - `access_policy = "read_only"`,
   - `validity.from = now`, `validity.to = now + horizon_days`,
   - parent edge `relation_type = "alternative_of"` pointing to the seed.

5. Schedule a verification job at `validity.to`. The job calls `dream__verify_counterfactual` which compares the recorded outcome against the next 7 days of relevant events.

6. Promotion rule:
   - if reality matches `predicted_outcome` within `cosine >= 0.75`, promote the branch to `type=process` and merge into the main graph via `promote_to_procedural()`,
   - else apply `confidence *= 0.7` and keep the branch in the garden for 30 more days,
   - after 3 unsuccessful verifications, prune the branch.

## Guardrails

Never generate counterfactuals on nodes tagged `sensitive=true` or `legal_hold=true`. The garden is for operational learning, not personal rumination.

Refuse if branches involve concrete recommendations to harm self or others. Log the refusal but do not store the prompt.

## Sortie utilisateur (FR)

Format compact, JSON-like, lisible:

```
Jardin contrefactuel - seed=11ce
1. action_alt="utiliser LiteLLM au lieu de FastMCP" -> outcome="latence -15ms mais typage flou" quality=0.62
2. action_alt="garder MCP officiel" -> outcome="stack standard, perte typage strict" quality=0.58
Verification programmee 2026-05-23.
```
