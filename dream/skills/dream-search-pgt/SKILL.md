---
name: dream-search-pgt
description: "Retrouver des souvenirs, décisions ou notes stockés dans la mémoire longue (Palais Graphique Temporel). Déclencher quand l'utilisateur demande \"tu te souviens de\", \"qu'est-ce que je sais sur\", \"cherche dans ma mémoire\", \"recall\", \"c'était quoi mon choix pour\", \"retrouve la note sur\", \"montre-moi ce que dream sait sur\". Supporte trois modes : index compact (défaut), timeline chronologique (--timeline {id}), contenu complet (--fetch {id1,id2,...}). Retourne les 5 résultats les plus pertinents avec date, type et score de vitalité."
---

# Dream Search PGT

Récupération contextuelle sur le graphe temporel via pipeline hybride : dense (bge-m3) + BM25 + spreading activation + cross-encoder rerank.

Trois couches progressives — toujours commencer par Layer 1, ne dérouler les couches suivantes que sur demande explicite ou quand l'utilisateur fournit un ID.

---

## Layer 1 — Index compact (défaut)

Sortie ~60 tokens pour 5 résultats. Produite systématiquement en premier.

**Étapes :**

1. Normaliser la requête : supprimer le métadiscours, développer les acronymes via le fichier glossary si disponible, garder au plus 32 tokens.
2. Appeler dream__search_semantic avec {"query": <normalisé>, "k": 25, "filters": {"vitality_min": 0.3}, "rerank": true}.
3. Pour les 10 meilleurs candidats, appeler dream__query_relations avec spreading_activation(seed_nodes, max_depth=3, decay=0.7) (voir references/activation-algorithm.md).
4. Fusionner et reranker. Retenir les 5 premiers avec final_score >= 0.55.
5. Afficher l'index compact. Si au moins un résultat, proposer Layer 2 ou Layer 3 en une ligne.

Format Layer 1 :

  [fact][0.82] 2026-04-12 — Marius préfère les exports CSV UTF-8 sans BOM. (id=8f2a)
  [decision][0.74] 2026-03-30 — Choix de FastMCP plutôt que LiteLLM. Cause : typage strict. (id=11ce)
  [process][0.61] 2026-02-18 — Pipeline consolidation : BM25 -> dense -> debate -> ledger. (id=3d7b)

  -> --timeline <id> pour le contexte chronologique  --fetch <id1,id2> pour le contenu complet

Un noeud par ligne. Excerpt tronqué à 80 chars. Pas d'introduction, pas de conclusion.

---

## Layer 2 — Timeline chronologique (--timeline <id>)

Déclenché quand l'utilisateur dit "montre le contexte autour de", "qu'est-ce qui s'est passé avant/après", ou cite un ID depuis Layer 1.

**Étapes :**

1. Appeler dream__get_node avec l'ID fourni pour obtenir validity.from et validity.to.
2. Appeler dream__search_semantic avec filtre de date +/-30 jours autour du noeud cible, k=10.
3. Trier par validity.from croissant. Encadrer le noeud cible avec les marqueurs AVANT / CIBLE / APRES.

Format Layer 2 :

  AVANT
  [fact][0.71] 2026-03-25 — Décision de ne pas utiliser Redis pour le cache. (id=09a1)
  [event][0.65] 2026-03-28 — Session de debug LanceDB schema v2. (id=2c4f)

  CIBLE
  [decision][0.74] 2026-03-30 — Choix de FastMCP. (id=11ce)

  APRES
  [fact][0.58] 2026-04-02 — FastMCP installé, latence MCP réduite de 40 %. (id=5e8d)

---

## Layer 3 — Contenu complet (--fetch <id1,id2,...>)

Déclenché quand l'utilisateur demande "montre-moi tout sur", "détails de", ou fournit des IDs explicites. Toujours produire Layer 1 en amont si ce n'est pas encore fait.

**Étapes :**

1. Pour chaque ID, appeler dream__get_node avec include_relations=true.
2. Afficher le noeud complet : content, source, vitality, confidence, validity, relations (max 5, triées par poids décroissant), meta.tags.

Format Layer 3 :

  id=11ce
  type       : decision
  vitality   : 0.74   confidence : 0.91
  date       : 2026-03-30
  content    : Choix de FastMCP plutôt que LiteLLM pour le routeur MCP.
               Raison : typage strict, pas de dépendance réseau.
  relations  : supersedes id=0a3c (LiteLLM draft, poids 0.9)
               implements  id=fe12 (architecture MCP v2, poids 0.7)
  source     : session:2026-03-30T14:22:11
  tags       : [architecture, mcp, dependency]

---

## Formule de score

  final_score = 0.45 * rerank_score
              + 0.25 * cosine_dense
              + 0.15 * bm25_normalised
              + 0.15 * activation_score

activation_score provient de spreading_activation, pondéré par temporal_recency() et relation_bonus() :
supersedes +0.2, contradicts -0.3, implements +0.1, neutre 0.0.

---

## Effets de bord

Chaque noeud récupéré est touché : le serveur incrémente meta.last_accessed et monte vitality via le terme Hebbian H(usage). Le skill n'appelle pas update_vitality manuellement.

---

## Règles de chaînage

- Layer 1 est toujours la première sortie, même si l'utilisateur a demandé --fetch directement.
- Layer 2 et Layer 3 ne sont proposés qu'après au moins un résultat en Layer 1.
- Si aucun candidat ne dépasse final_score >= 0.55, retourner un résultat vide explicite. Ne pas inventer.
- En mode --explain, ajouter le chemin d'activation après chaque ligne du Layer 1 : via id=3d7b (spreading 0.61) -> id=11ce.

---

## Refus

Si la requête contient des mots-clés type "tous mes mots de passe", "secrets", "tokens API", refuser et expliquer. La PGT ne doit pas servir d'oracle de fuite.
