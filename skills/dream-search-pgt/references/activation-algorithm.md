# Spreading Activation Reference

## Pseudo-code

```python
def spreading_activation(seed_nodes, max_depth=3, decay=0.7):
    scores = {n: 1.0 for n in seed_nodes}
    frontier = [(n, 1.0, 0) for n in seed_nodes]
    while frontier:
        curr, score, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        for neighbor, edge in graph.get_edges(curr):
            edge_weight = (
                edge.weight
                * edge.temporal_recency()
                * edge.relation_bonus()
            )
            new_score = score * edge_weight * (decay ** depth)
            if neighbor not in scores or new_score > scores[neighbor]:
                scores[neighbor] = new_score
                if graph.nodes[neighbor].vitality > 0.3:
                    frontier.append((neighbor, new_score, depth + 1))
    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
```

## Edge helpers

`temporal_recency()` returns `exp(-0.02 * days_since(edge.temporal_bounds.valid_from))`.

`relation_bonus()` returns:
- `supersedes`: 1.2,
- `implements`: 1.1,
- `depends_on`: 1.0,
- `contradicts`: 0.5,
- default: 1.0.

## Pruning

Stop the propagation at depth 3 by default. Increase to 4 only when the user explicitly asks for "deep dive". The cost is quadratic in the neighbourhood factor.

Nodes whose vitality is below 0.2 are not enqueued. They remain reachable only via direct semantic match.

## Complexity

Average wall time on a 50k node graph: 12 ms (NetworkX in-memory). P95: 45 ms. The reranker dominates the budget, not the propagation.
