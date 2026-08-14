# Vitality Formula Reference

## Parametric form

```
V(t+1) = alpha * exp(-lambda * dt)
       + beta  * H(usage)
       + gamma * R(goal)
       - delta * C
```

Calibrated defaults:

- `alpha = 0.30`,
- `beta  = 0.35`,
- `gamma = 0.30`,
- `delta = 0.10`,
- `lambda = 0.05` (day-scale).

`dt` is the number of days since `meta.last_accessed`.

## Terms

`H(usage) = log(1 + access_count + co_activation_score)` where `co_activation_score` is the sum of activation scores received during the last 7 spreading-activation runs.

`R(goal) = cosine(node.embedding, active_goals_embedding)` clipped to `[0, 1]`. `active_goals_embedding` is the mean of the embeddings of the top 5 nodes flagged `type=decision` with `vitality > 0.7` in the past 30 days.

`C` is the contradiction count weighted by edge confidence: `C = sum(edge.confidence for edge in incoming_contradicts_edges)`.

## Action thresholds

| Range | Tier | Action |
|-------|------|--------|
| V > 0.85 | hot | Redis cache, surfaced in `CLAUDE.md` |
| 0.4 <= V <= 0.85 | active | NetworkX graph, indexed in LanceDB |
| 0.2 <= V < 0.4 | dim | LanceDB only, dropped from graph traversal |
| V < 0.2 | cold | Moved to `archive/cold/<date>.jsonl` |

## Floor and ceiling

`V` is clipped to `[0.0, 1.0]`. Nodes whose vitality stays below 0.05 for 90 consecutive days are eligible for hard deletion, but only after explicit user confirmation.
