---
name: dream-explain-node
description: "Diagnostiquer un nœud de mémoire Dream en détail. Déclencher quand l'utilisateur dit 'pourquoi ce souvenir n'est plus là', 'explique le nœud id=...', 'debug ce souvenir', 'pourquoi ce nœud est cold', 'inspecte la mémoire sur X', 'vitality de ce nœud', 'dream explain'. Retourne un snapshot complet : vitality fraîche, tier, accès, edges, historique de consolidation et raison de l'état actuel."
---

# Dream Explain Node

Outil de débogage transparent pour inspecter un nœud PGT sans toucher la base.

## Instructions pour Claude

### Étape 1 — Résoudre l'identifiant

L'utilisateur peut donner :
- Un `id` court ou complet (ex. `8f2a`, `8f2a3c91-...`).
- Une description ("le nœud sur FastMCP", "ma décision sur les exports CSV").

Si c'est une description, appeler d'abord `dream__search_semantic` avec `k=3` pour trouver le node_id le plus probable, confirmer avec l'utilisateur si ambigu.

### Étape 2 — Appeler explain_node

```python
result = dream__explain_node(node_id="<id>")
```

Si `result["status"] == "not_found"` : informer l'utilisateur et proposer de lancer `dream__search_semantic`.

### Étape 3 — Interpréter et expliquer

Construire une réponse lisible à partir du JSON retourné. Règles d'interprétation :

**Vitality**
- `vitality_fresh` diverge de `vitality_stored` de plus de 0.15 → signale que la dernière GC est stale, suggérer de forcer un cycle.
- `tier == "cold"` → expliquer : "Ce nœud n'a pas été consulté depuis longtemps et/ou son contenu n'est plus aligné avec tes objectifs récents."
- `tier == "hot"` avec `last_accessed` > 30 jours → anomalie : le nœud a eu beaucoup d'accès anciens mais plus récemment. La recency correction devrait l'avoir rétrogradé — si ce n'est pas le cas, signaler.

**Edges**
- Présence d'arêtes `contradicts` entrant : "Ce nœud est contredit par {from_ids}. Son score de contradiction pèse -{contradiction_weight * DELTA} sur la vitality."
- Présence d'arêtes `supersedes` sortant : "Ce nœud a été supersédé par {to_ids}, ce qui explique sa vitality basse."

**Consolidation**
- `last_consolidation.decision == "rejected"` → "Rejeté lors du cycle du {date} avec score={score}. Cause probable : consensus trop bas (seuil 0.5)."
- `last_consolidation.decision == "hitl"` → "En attente d'arbitrage humain depuis {created_at}. Utiliser dream-admin Section 4 pour trancher."
- `last_consolidation == null` → "Ce nœud n'a jamais traversé un cycle de consolidation (créé après la dernière consolidation ou vitality < 0.3 lors du filtrage)."

**HITL**
- Si présent et non résolu : "Arbitrage en attente depuis {created_at}. Score débat : {score}."

### Étape 4 — Suggestions d'action

Toujours terminer par 1 à 2 suggestions concrètes selon l'état :

| État | Suggestion |
|------|-----------|
| cold, jamais consulté | `dream-store-event` pour enrichir le nœud avec de nouvelles preuves |
| rejected, score 0.4-0.5 | Reformuler le contenu et re-stocker avec `dream-store-event` (relation_hints supersedes) |
| hitl pending | `dream-admin` Section 4 pour résoudre l'arbitrage |
| stale vitality | `dream-admin` Section 2 pour forcer un cycle GC |
| contradits | Consulter les nœuds contradicteurs via `dream__explain_node` sur leurs ids |

## Format de sortie (FR)

Bloc compact, sans titre générique, directement actionnable :

```
Nœud 8f2a — [decision] — tier: active
Contenu : "Choix de FastMCP plutôt que LiteLLM pour le routeur."

Vitalité : 0.71 (stockée) / 0.68 (fraîche) — stable
Accès : 4 fois, dernier accès 2026-04-30
Alignement objectif : 0.72 (fort — cohérent avec les décisions récentes)

Edges sortants : implements → 3c7d (weight 0.8)
Edges entrants : aucun contradicts actif

Consolidation 2026-05-01 : accepté (score 0.74)
HITL : aucun

Suggestion : nœud sain, rien à faire.
```

Aucune introduction. Aucune conclusion sur "l'état général de la mémoire" sauf si demandé.

## Refus

Refuser si le nœud a `access_policy = "private"` ou si l'utilisateur demande d'inspecter plusieurs nœuds à la fois sans préciser le contexte (risque de dump massif). Dans ce cas, proposer d'utiliser `dream-search-pgt` à la place.
