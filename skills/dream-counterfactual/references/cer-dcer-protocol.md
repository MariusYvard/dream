# CER / DCER Protocol Reference

CER stands for Counterfactual Episodic Replay. DCER adds Deliberative scoring before the branch is committed.

## Branch generation prompt

```
You are the Counterfactual Generator. Given the seed event and the local
context (graph neighbours, last 3 related decisions), produce 2 or 3
alternative actions that could have been taken at the same decision point.

Hard rules:
- Each branch must specify {action_alt, predicted_outcome, preconditions, horizon_days}.
- horizon_days is between 1 and 30.
- Do not invent new entities outside the provided context.
- Output strict JSON, no preamble, no trailing prose.

Seed:
{seed_json}

Context neighbours:
{neighbours_json}
```

## Critique prompt

```
You are the Critique. Score each branch on three axes (0..1):
- risk_score: probability of avoiding the original failure mode,
- coherence: alignment with existing graph patterns,
- alignment_with_goals: alignment with user's active goals.

Output JSON: {"scores": [{"branch_id": ..., "risk_score": ..., "coherence": ..., "alignment_with_goals": ...}]}
```

## Verification job

The verifier runs once at `validity.to`. It pulls all events between `seed.validity.from` and `now`, filters those whose embedding is close to `predicted_outcome.embedding` (cosine >= 0.6), and computes a match score.

```
match_score = max(cosine(predicted_outcome, e.embedding) for e in window_events)
```

- `match_score >= 0.75`: promote branch.
- `0.5 <= match_score < 0.75`: keep, decay confidence by 0.3.
- `match_score < 0.5`: prune.

## Promotion to procedural

When a branch is promoted, its representation is rewritten in imperative form ("If precondition X, do action_alt") and stored under `type=process`. A `derived_from` edge points back to the original seed.
