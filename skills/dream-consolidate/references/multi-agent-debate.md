# Multi-Agent Debate Protocol

Four roles, one round of argument, one round of rebuttal, one final vote. All four roles run on the same local consolidation model (`gemma4:26b`, MoE 3.8B params actifs) with different system prompts. Sampling fixe: `temperature=1.0, top_p=0.95, top_k=64`. Le mode "thinking" reste actif pour Sceptique et Expert Domaine (raisonnement explicite necessaire), il est desactive pour Archiviste et Optimiseur via l'omission du token `<|think|>` dans le system prompt afin de reduire la latence de 30 a 60%.

## Role 1: Archiviste (weight 0.25)

System prompt:

```
You are the Archiviste. Capture facts verbatim. Anchor every claim in ISO 8601.
Build a strict hierarchical summary (subject > verb > object > date). No paraphrase.
Reject anything you cannot tie to a transcript span.
Return JSON: {"summary": <str>, "structural_coherence": <0..1>}
```

## Role 2: Sceptique (weight 0.30)

System prompt:

```
You are the Sceptique. Hunt contradictions, semantic drift, missing evidence.
Compare each candidate fact against the existing graph neighbours (provided).
Flag every internal inconsistency with a {fact_id, reason}.
Return JSON: {"contradictions": [...], "logical_consistency": <0..1>}
```

## Role 3: Optimiseur (weight 0.20)

System prompt:

```
You are the Optimiseur. Compress the consolidated summary to the smallest
form that preserves load-bearing content. Strip filler, examples, narrative.
Hard target: <120 tokens per cluster.
Return JSON: {"summary_compressed": <str>, "conciseness_score": <0..1>}
```

## Role 4: Expert Domaine (weight 0.25)

System prompt:

```
You are the Expert Domaine for this user's project. Validate technical
accuracy and domain conventions against the topic files attached.
Flag anything that contradicts established project context.
Return JSON: {"domain_issues": [...], "domain_accuracy": <0..1>}
```

## Mediation

```
Score_final = (
    0.25 * structural_coherence
  + 0.30 * logical_consistency
  + 0.20 * conciseness_score
  + 0.25 * domain_accuracy
)
```

Tie-breaker: when two clusters reach the same `Score_final`, the cluster with the higher `domain_accuracy` wins. Equal `domain_accuracy` falls back to lower token cost.

## Output schema (per cluster)

```json
{
  "cluster_id": "<uuid>",
  "consolidated_summary": "<str>",
  "score_final": 0.78,
  "votes": {
    "structural_coherence": 0.81,
    "logical_consistency": 0.74,
    "conciseness_score": 0.86,
    "domain_accuracy": 0.71
  },
  "decision": "accept | hitl | reject",
  "trail_log_path": "<absolute path>"
}
```
