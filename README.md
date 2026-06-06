# Dream

Plugin Cowork qui donne à Claude une mémoire persistante avec un cycle de sommeil. Tout tourne sur ta machine, aucun transcript brut ne sort.

## Le plugin en une phrase

À la fin de chaque session, Claude écrit ce qu'il vient d'apprendre dans un dossier local. La nuit (02:05), un processus relit ces notes, les compare à ce que Claude savait déjà, garde ce qui est utile, supprime le bruit, et reconstruit un index court qui sera rechargé au début de la session suivante.

## L'idée en analogie avec la mémoire humaine

Tu vis une journée, tu accumules des conversations, des décisions, des erreurs. La nuit, ton cerveau ne réécoute pas tout, il trie. Il renforce ce qui revient souvent, il oublie ce qui n'a servi à rien, il rapproche les souvenirs qui se ressemblent. Au réveil, tu as un index résumé. Dream fait la même chose pour Claude.

Les trois phases du cerveau qu'on imite :

| Phase humaine | Équivalent Dream |
|---|---|
| Encodage (vivre la journée) | Capture des évènements dans un buffer JSONL après chaque session |
| Consolidation (sommeil paradoxal + lent) | Cycle nocturne à 02:05 qui rejoue le buffer, fusionne les notes, écrit dans le graphe |
| Récupération (se souvenir le lendemain) | Recherche hybride dans le graphe au début de la session suivante |

## Ce que ça résout concrètement

Sans Dream, Claude redémarre chaque conversation à zéro. Tu lui réexpliques le contexte du projet à chaque fois, tu lui redonnes les conventions, tu redis "non, pas comme ça, plutôt comme on avait décidé hier".

Avec Dream :
- Claude se souvient de tes décisions de projet (architecture, conventions, choix techniques)
- Il sait qui sont les personnes dont tu parles
- Il garde les corrections que tu as faites
- Il oublie automatiquement les bavardages qui n'ont pas servi
- Tout reste sur ta machine, pas d'envoi cloud

## Comment c'est rangé : le Palais Graphique Temporel

C'est un graphe (réseau de nœuds reliés par des arêtes) avec une dimension temporelle. Chaque nœud est un fait, une décision, un bout de code, une personne, un processus ou une erreur. Chaque arête dit comment deux nœuds sont reliés (l'un implémente l'autre, l'un en dépend, l'un contredit l'autre, l'un remplace l'autre).

Trois stockages travaillent ensemble :

| Stockage | Rôle | Format |
|---|---|---|
| SQLite (`pgt.sqlite`) | Métadonnées, vitalité, ledger | base relationnelle classique |
| LanceDB (`vectors.lance/`) | Vecteurs sémantiques pour la recherche par similarité | base vectorielle |
| NetworkX (`graph.gpickle`) | Le graphe en mémoire pour les parcours | structure en RAM |

## La vitalité, c'est quoi

Chaque nœud a un score de vitalité entre 0 et 1. Plus il est élevé, plus le nœud est "vivant" et facile d'accès. La formule combine quatre signaux :

- décroissance temporelle (plus c'est ancien, plus ça baisse)
- usage récent (plus on l'a consulté, plus ça remonte)
- alignement avec les objectifs actuels (plus c'est pertinent pour ce que tu fais en ce moment, plus ça remonte)
- contradictions reçues (si un nouveau fait le contredit, ça baisse)

Le résultat décide où le nœud habite :

| Score | Statut | Comportement |
|---|---|---|
| V > 0.85 | chaud | Mis en cache, surfacé dans l'index du jour |
| 0.4 <= V <= 0.85 | actif | Dans le graphe, accessible normalement |
| 0.2 <= V < 0.4 | sombre | Indexé mais hors parcours du graphe |
| V < 0.2 | froid | Archivé sur disque, sortie automatique |

## Comment Claude se souvient : la recherche hybride

Quand Claude cherche "qu'est-ce que je sais sur X", quatre méthodes tournent en parallèle puis sont mélangées :

| Méthode | Ce qu'elle capte |
|---|---|
| BM25 | Les mots exacts |
| Embeddings (bge-m3) | Le sens, même avec des mots différents |
| Propagation d'activation | Les voisins du graphe, même non recherchés directement |
| Cross-encoder (ms-marco) | Le réordonnancement final pour ne garder que ce qui colle vraiment |

Le score final pondère ces quatre signaux et garde les 5 meilleurs résultats.

## Le débat des 4 rôles pendant le sommeil

Pendant le cycle nocturne, chaque groupe de notes à consolider passe devant un mini-tribunal interne. Quatre instances du même modèle (`gemma4:12b`) jouent chacune un rôle différent, votent, et un score consensus décide si la note est intégrée :

| Rôle | Mission | Poids du vote |
|---|---|---|
| Archiviste | Capture les faits, ancre les dates en ISO 8601, refuse les paraphrases | 25% |
| Sceptique | Chasse les contradictions et les preuves manquantes | 30% |
| Optimiseur | Compresse à moins de 120 tokens par cluster | 20% |
| Expert Domaine | Vérifie la cohérence avec le contexte projet | 25% |

Si le score consensus dépasse 0.7, la note est acceptée. Entre 0.5 et 0.7, elle est mise en file HITL (Human In The Loop, c'est-à-dire qu'on te demandera ton avis). En dessous de 0.5, elle est rejetée et le débat est archivé pour analyse.

## Le jardin contrefactuel

Quand un nœud est tagué comme une erreur ou que son score de consensus est très bas, Dream peut générer 2 ou 3 branches "et si on avait fait autrement". Ces branches sont stockées en lecture seule, avec une date d'expiration. Au bout de la fenêtre, Dream compare ce qui s'est réellement passé avec ce que la branche prédisait. Si la branche avait raison, elle est promue en processus stable. Sinon, elle décay ou elle est élaguée.

## Le ledger crypto

Chaque écriture dans le graphe est signée avec une clé Ed25519 locale et ajoutée à une chaîne Merkle. Si jamais le fichier `pgt.sqlite` est corrompu ou trafiqué, la vérification Merkle détecte la modification en moins de 100 ms. Restauration ponctuelle en moins de 5 secondes par rejeu de la chaîne.

## Les trois modes de fonctionnement (circuit breaker)

> Exemption bootstrap (v0.5.0) : un graphe vide (0 nœud actif) affiche une vitalité moyenne de 0.0 par construction. Ce cas ne déclenche plus SECURISE, sinon la consolidation (seule voie autonome de peuplement du graphe) resterait verrouillée derrière le breaker. Le déclencheur vitalité s'applique dès le premier nœud actif.

Dream surveille en continu sa propre santé. Trois modes possibles :

| Mode | Déclenchement | Comportement |
|---|---|---|
| NORMAL | tout va bien | Pipeline complet : débat + contrefactuel + recherche complète |
| CONSERVATEUR | latence p95 > 500 ms ou consensus < 0.7 | Lecture seule, quorum réduit, contrefactuel coupé |
| SÉCURISÉ | RAM > 15 Go, vitalité moyenne < 0.4 ou ledger Merkle KO | Arrêt débat et contrefactuel, HITL obligatoire, consolidation différée |

Pour remonter d'un cran, il faut 3 sondes vertes consécutives. Pas de bascule brutale en boucle.

## Stack logicielle

Modèles servis via Ollama, tous locaux :

| Modèle | Rôle | RAM active | Disque |
|---|---|---|---|
| `gemma4:e4b` | Sanitisation (P95 cible <500 ms) | ~5 Go | 9.6 Go |
| `gemma4:12b` | Consolidation et génération contrefactuelle | ~7.5 Go | 16 Go |
| `bge-m3` | Embeddings (1024 dimensions) | ~3 Go CPU | 2 Go |
| `ms-marco-MiniLM-L-6-v2` | Reranking | <0.5 Go | 90 Mo |

Sampling Gemma 4 fixé : `temperature=1.0, top_p=0.95, top_k=64`. Mode "thinking" géré via le token `<|think|>` dans le system prompt.

## Sécurité : tout reste local

| Garantie | Mécanisme |
|---|---|
| Aucun transcript brut ne sort de la machine | Sanitisation par `gemma4:e4b` + 17 regex déterministes (clés AWS, GitHub, Anthropic, OpenAI, JWT, IBAN, IP, email, etc.) |
| Détection de fuite involontaire | Hash SHA-256 de l'input comparé à une deny-list |
| Intégrité de la mémoire | Ed25519 + Merkle, vérifié à chaque cycle |
| Réseau verrouillé | FastMCP bound sur `127.0.0.1` |

## Installation

Pré-requis : Windows 10/11, Linux ou macOS, 16 Go RAM minimum, 35 Go de disque libre, Ollama installé.

```bash
# 1. dépendances Python
pip install -r requirements.txt

# 2. modèles Ollama
ollama pull gemma4:e4b
ollama pull gemma4:12b
# bge-m3 et ms-marco se téléchargent automatiquement au premier import

# 3. variables d'environnement (optionnel)
export DREAM_HOME="$HOME/.dream"

# 4. enregistrement plateforme
# Windows :  python scripts/setup_windows.py
# Linux   :  python scripts/setup_linux.py
# macOS   :  python scripts/setup_macos.py

# 5. bootstrap dans Claude
# Demande dans le chat : "initialise dream"
# Le skill dream-init prépare le stockage et signe la clé Ed25519
```

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `DREAM_HOME` | `~/.dream` | Racine des données |
| `DREAM_REDIS_HOST` | `127.0.0.1` | Cache hot (fallback mémoire si Redis absent) |
| `DREAM_REDIS_PORT` | `6379` | Cache hot |
| `DREAM_CONSOLIDATION_MODEL` | `gemma4:12b` | Modèle Ollama pour le débat |
| `DREAM_COUNTERFACTUAL_MODEL` | `gemma4:12b` | Modèle Ollama pour le jardin |
| `DREAM_METRICS_PORT` | `9464` | Port de l'endpoint Prometheus |
| `DREAM_METRICS_ENABLED` | `1` | Mettre à `0` pour couper l'endpoint Prometheus |

## Les 10 skills

| Skill | À quoi sert |
|---|---|
| `dream-init` | Bootstrap initial : pull des modèles, init schéma, signature de la clé Ed25519 |
| `dream-store-event` | Ajouter un fait dans le graphe (sanitisé puis signé) |
| `dream-search-pgt` | Recherche hybride sur le graphe |
| `dream-consolidate` | Lancer le cycle nocturne à la demande (autrement automatique à 02:05) |
| `dream-counterfactual` | Faire pousser une branche alternative sur une erreur |
| `dream-sanitize` | Nettoyer un texte avant stockage (regex + LLM local) |
| `dream-health` | État de santé, métriques, mode du circuit breaker |
| `dream-load-context` | Préparer un bundle de contexte pour la session courante |
| `dream-admin` | Diagnostic, réparation, cycle forcé, scénarios contrefactuels, arbitrages HITL |
| `dream-explain-node` | Inspection détaillée d'un nœud (vitalité, tier, edges, historique) |

## Hooks et scheduler

Au démarrage d'une session Claude, le hook `SessionStart` charge automatiquement les meilleurs nœuds du graphe (limité à 2k tokens). À la fin, le hook `Stop` extrait les phrases load-bearing de la conversation et les pousse dans le buffer du jour. Aucune action manuelle requise.

Le scheduler (`scripts/scheduler.py`) lance le cycle nocturne à 02:05 local. Sur Linux : `systemd --user`. Sur macOS : `launchd`. Sur Windows : le Planificateur de tâches.

## Outils MCP exposés

10 outils côté serveur FastMCP :

`store_event`, `search_semantic`, `query_relations`, `update_vitality`, `propose_counterfactual`, `sanitize_local`, `load_context`, `health_check`, `set_mode`, `verify_counterfactual`.

## Observabilité

Le serveur expose Prometheus sur `127.0.0.1:9464`. Métriques clés :

| Métrique | Type | Utilité |
|---|---|---|
| `dream_cycle_completed_total` | counter | Nombre de cycles nocturnes réussis |
| `dream_cycle_failed_total{phase}` | counter | Cycles qui ont échoué et à quelle phase |
| `dream_sanitize_latency_ms` | histogram | Distribution de la latence de sanitisation |
| `dream_search_latency_ms` | histogram | Distribution de la latence de recherche |
| `dream_consensus_score` | histogram | Score consensus par cluster pendant le débat |
| `dream_hitl_pending` | gauge | Nombre de notes en attente d'arbitrage humain |
| `dream_ram_peak_mb` | gauge | Pic RAM observé par le daemon |
| `dream_vitality_avg` | gauge | Vitalité moyenne du graphe |
| `dream_circuit_mode` | gauge | Mode actif (0=NORMAL, 1=CONSERVATEUR, 2=SÉCURISÉ) |
| `dream_ledger_merkle_ok` | gauge | 1 si l'intégrité Merkle est vérifiée, 0 sinon |
| `dream_ollama_up` | gauge | 1 si le daemon Ollama local répond, 0 sinon |

## Une journée type

| Heure | Évènement |
|---|---|
| 09:00 | Tu ouvres une session Claude. Le hook SessionStart charge l'index du jour (~500 tokens) dans le contexte. |
| Pendant | Claude répond, tu corriges, vous décidez ensemble. Le hook Stop pousse les phrases utiles dans le buffer dès que tu fermes. |
| 02:05 | Le scheduler lance le cycle nocturne. ~10 minutes de débat 4 rôles, écriture du graphe, mise à jour de la vitalité, rebuild de l'index. |
| Lendemain | Tu ouvres une nouvelle session. L'index a été reconstruit avec les apprentissages d'hier. |

## Dépannage Windows (terrain)

Pièges rencontrés en production sur Windows 11, avec leur lecture correcte :

| Symptôme | Lecture correcte |
|---|---|
| `MCP error -32001` sur `store_event` ou `load_context` | Avant v0.5.0, l'écriture aboutissait souvent côté serveur quelques minutes après le timeout client. Réutiliser le même `id` (uuid4) en retry est idempotent. Vérification : `SELECT COUNT(*) FROM nodes`. Le préchargement de l'embedder (v0.5.0) fait disparaître le cas, désactivable via `DREAM_PRELOAD_EMBEDDER=0`. |
| `can't open file ... scheduler.py` dans `logs/nightly.log` | La tâche planifiée pointe vers un cache de plugin supprimé. Relancer `python scripts/setup_windows.py` : depuis v0.5.0 il déploie une copie stable dans `DREAM_HOME\scripts` et enregistre tout contre elle. |
| `CantActivateDocumentInPipeline` dans un shell spawné | PATH et PATHEXT vides dans certains shells automatisés (Desktop Commander). Préfixer chaque commande : `$env:PATHEXT=".COM;.EXE;.BAT;.CMD"; $env:PATH="$env:SystemRoot\system32;$env:SystemRoot;$env:PATH"`. |
| Un outil filesystem voit `%USERPROFILE%\.dream` vide | Faux négatif observé (taille 0, aucun enfant listé) alors que le dossier est peuplé. Trancher avec `Get-ChildItem -Force` dans PowerShell avant tout diagnostic. |
| Fichiers vus tronqués depuis un sandbox monté sur OneDrive | Désynchronisation OneDrive. Lire et écrire depuis la machine locale, ou passer par git. |
| Erreur 412 d'Ollama sur `gemma4:12b` | Version d'Ollama trop ancienne. Requiert >= 0.30.3. |
| RAM saturée pendant le cycle | `gemma4:12b` chargé laisse environ 0.3 Go libres sur une machine 16 Go. Fermer les applications lourdes avant 02:05 ou définir `DREAM_PRELOAD_EMBEDDER=0`. |
| Sortie Python invisible en arrière-plan | Les subprocess PowerShell avalent stdout sous redirection. Écrire les résultats dans des fichiers, jamais sur stdout. |

## Licence

MIT.
