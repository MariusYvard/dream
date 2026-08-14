---
name: dream-admin
description: "Diagnostiquer, réparer ou explorer la mémoire Dream. Déclencher quand l'utilisateur demande 'état de dream', 'ça tourne ?', 'dream health', 'force le cycle', 'et si on avait fait autrement', 'explore l'alternative', 'what if', 'HITL', 'envoie le digest', 'arbitrages en attente'. Regroupe quatre actions : vérifier l'état du système, forcer un cycle de consolidation manuel, générer des scénarios alternatifs sur une décision passée, gérer les arbitrages HITL via Gmail."
---

# Dream Admin

Skill de maintenance et d'exploration. Route vers l'une des quatre actions selon l'intention.

## Routage

- **Santé** — "état de dream", "ça tourne ?", "dream health", "est-ce que dream est ok" → Section 1.
- **Cycle manuel** — "force le cycle", "consolide maintenant", "le scheduler n'a pas tourné" → Section 2.
- **Contrefactuel** — "et si on avait fait autrement", "explore l'alternative", "what if" → Section 3.
- **HITL / digest** — "arbitrages en attente", "HITL", "envoie le digest", "traite les pending" → Section 4.

En cas d'ambiguïté, demander une ligne de clarification avant d'agir.

---

## Section 1 — Health Check

1. Appeler `dream__health_check`. Réponse attendue :

```json
{
  "uptime_s": 91230,
  "mode": "NORMAL | CONSERVATEUR | SECURISE",
  "ram_peak_mb": 13420,
  "ram_current_mb": 9210,
  "latency_p50_ms": 84,
  "latency_p95_ms": 287,
  "consensus_rate_24h": 0.78,
  "vitality_avg": 0.61,
  "ledger_merkle_ok": true,
  "models_loaded": ["gemma4:e4b", "bge-m3"],
  "circuit_state": "closed | half_open | open",
  "hitl_pending": 3
}
```

2. Vérifier les seuils du circuit breaker :

| Mode | Déclencheur |
|------|------------|
| NORMAL | tout vert |
| CONSERVATEUR | latency_p95 > 500 ms ou consensus_rate < 0.7 |
| SECURISE | vitality_avg < 0.4 ou ledger_merkle_ok == false ou ram_peak > 15 000 |

Si le mode retourné diverge du mode calculé, corriger via `dream__set_mode`.
Si `ledger_merkle_ok` est `false`, passer immédiatement en SECURISE.
Si `hitl_pending >= 5`, alerter l'utilisateur et suggérer Section 4.

3. Sortie compacte :

```
Dream health 2026-05-16 14:02
- mode: NORMAL (uptime 1j 1h)
- RAM: 9.2 / 13.4 Go pic
- latence: p50 84 ms, p95 287 ms
- consensus 24h: 78% | vitalité moyenne: 0.61
- ledger: ok
- HITL en attente: 3
- modèles: gemma4:e4b, bge-m3
```

---

## Section 2 — Consolidation manuelle

Utiliser uniquement si le cycle automatique de 02h05 n'a pas tourné ou si l'utilisateur le demande explicitement.

1. Vérifier les pré-conditions : mode NORMAL, buffer du jour présent, dernière consolidation > 18h.
2. Exécuter le pipeline en 4 phases (Orientation → Signal Gathering → Consolidation → Garbage Collection) tel que défini dans `dream-consolidate`.
3. Sortie synthétique, 10 lignes max :

```
Cycle de rêve 2026-05-16 14:05–14:14
- Phase 1: 4 drifted_facts détectés
- Phase 2: 38 événements retenus (sur 142)
- Phase 3: 27 acceptés, 4 HITL, 7 rejetés
- Phase 4: 12 démotions, 3 promotions
- RAM pic: 13.8 Go. Ledger root: 4a91...
```

Refuser si mode SECURISE actif, intégrité ledger compromise, ou moins de 3 événements load-bearing dans le buffer.

---

## Section 3 — Contrefactuel

1. Identifier le nœud seed : type=error, consensus_score < 0.4, ou référence explicite de l'utilisateur.
2. Générer 2-3 branches alternatives via `gemma4:26b`. Format attendu :

```json
{
  "branches": [
    {
      "action_alt": "<ce qu'on aurait pu faire>",
      "predicted_outcome": "<conséquence attendue>",
      "preconditions": ["<liste>"],
      "horizon_days": 7
    }
  ]
}
```

3. Évaluer chaque branche : `branch_quality = 0.4*risk + 0.3*coherence + 0.3*alignment`.
4. Stocker les branches acceptées (quality >= 0.55) en lecture seule avec `relation_type = "alternative_of"`.
5. Programmer une vérification automatique à `validity.to` via `dream__verify_counterfactual`.
6. Règle de promotion : si la réalité correspond (cosine >= 0.75) → promouvoir en `type=process`. Sinon `confidence *= 0.7`, garder 30 jours. Après 3 échecs → supprimer.

Refuser sur les nœuds `sensitive=true` ou `legal_hold=true`.

Sortie compacte :

```
Contrefactuel - seed=11ce
1. "utiliser LiteLLM" -> "latence -15ms mais typage flou" quality=0.62
2. "garder MCP officiel" -> "stack standard, perte typage strict" quality=0.58
Vérification programmée 2026-05-23.
```

---

## Section 4 — HITL digest Gmail

Traiter les arbitrages humains qui bloquent la consolidation.

### 4a — Lister les items en attente

1. Appeler `dream__get_hitl_pending` (limit=20).
2. Si `count == 0` : répondre "Aucun arbitrage en attente." et s'arrêter.
3. Afficher un résumé tabulaire :

```
HITL en attente (3 items)
#  id        type      score  contenu (extrait)
1  8f2a      fact      0.63   "Marius préfère les exports CSV..."
2  11ce      decision  0.58   "Choix de FastMCP plutôt que..."
3  2b9d      process   0.51   "Convention de nommage des topics..."
```

### 4b — Envoyer le digest par email

Si l'utilisateur demande explicitement d'envoyer le digest ou si `count >= 3` :

1. Utiliser `dream__get_hitl_pending` pour récupérer les items.
2. Formater le sujet : `[Dream HITL] {count} arbitrage(s) en attente — {date}`.
3. Construire le corps selon le template de `scripts/hitl_webhook.py → build_digest_email`.
4. Appeler le MCP Gmail `gmail_create_draft` avec :
   - `to`: valeur de `DREAM_HITL_EMAIL` (défaut: mariusyvard72@gmail.com)
   - `subject`: le sujet formaté
   - `body`: le corps Markdown
5. Confirmer : "Draft Gmail créé : {count} arbitrages. Sujet : {subject}".

Ne pas envoyer directement (gmail_send) — toujours créer un draft pour validation.

### 4c — Résoudre un item

Si l'utilisateur dit "accepte id=8f2a", "rejette 11ce" ou "snooze 2b9d" :

1. Mapper la commande vers `decision` : accepte→accept, rejette→reject, snooze/defer→defer.
2. Appeler `dream__resolve_hitl` avec `hitl_id` et `decision`.
3. Confirmer : "✓ 8f2a — accepté. Nœud promu en status=active."

### 4d — Traitement en lot

Si l'utilisateur dit "traite tout" ou "résous tous les HITL" :

1. Récupérer les items via `dream__get_hitl_pending`.
2. Pour chaque item avec `score >= 0.65` : appeler `dream__resolve_hitl` avec decision=accept.
3. Pour chaque item avec `score < 0.50` : appeler `dream__resolve_hitl` avec decision=reject.
4. Les items entre 0.50 et 0.65 : les lister et demander confirmation individuelle.
5. Rapport final :

```
HITL batch résolu
- Acceptés automatiquement (score >= 0.65) : 2
- Rejetés automatiquement (score < 0.50)   : 1
- En attente de décision manuelle          : 1
```
