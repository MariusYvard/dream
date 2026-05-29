# Audit du plugin Dream (v0.2.2)

Date : 2026-05-29. Périmètre : code, configuration, packaging, autonomie, état de préparation GitHub.

## Verdict

Le plugin est mûr. Documentation complète, packaging propre, suite de tests verte (31 tests passent sans la stack ML), aucun TODO ni stub dans les 3069 lignes de scripts, aucun fichier de données ou secret présent dans l'arbre. Deux écarts d'autonomie réels empêchent le plugin de tenir entièrement la promesse du README. Quelques ajouts standards manquent avant un push public.

## Ce qui est déjà solide

La séparation des responsabilités est nette (buffer, sanitisation, ledger, débat, vitalité, circuit breaker, scheduler, serveur MCP). La sanitisation est correctement placée dans `dream_buffer.append_event`, donc tout évènement traverse le filtre regex plus LLM avant d'atteindre le disque, y compris via le hook Stop. Le fail-safe est bon : si Ollama est absent, `sanitize_local` retombe sur le passage regex seul au lieu de laisser fuiter du texte brut. Les écritures suivent un ordre délibéré (vecteur LanceDB puis métadonnées SQLite puis graphe), ce qui évite les métadonnées orphelines. Le `.gitignore` exclut bien `keys/`, `*.private`, `pgt.sqlite`, `vectors.lance/`, `buffer/`, `archive/`, `rejected/`, `circuit.json` et `CLAUDE.md`. Les versions sont cohérentes entre `plugin.json`, `marketplace.json` et le CHANGELOG (0.2.2).

## Écarts d'autonomie (priorité haute)

### 1. La vitalité n'est jamais recalculée par le cycle nocturne

`scheduler.py` importe `VitalityInputs`, `compute as vitality_compute` et `embedder` mais ne les utilise nulle part dans `run_cycle`. Le cycle lit la colonne `vitality` telle quelle puis archive les nœuds dont le tier est déjà froid. Or la colonne n'est mise à jour que par l'outil MCP `update_vitality`, déclenché à l'accès ou manuellement. Conséquence : la décroissance temporelle décrite dans le README (l'oubli automatique du bruit) ne se produit pas seule. Un nœud ancien jamais reconsulté garde sa vitalité d'origine et ne descend jamais vers le tier froid.

Correctif : dans `run_cycle`, après la phase de débat, parcourir les nœuds `scenario='base'`, recalculer la vitalité via `vitality_engine.compute` (en alimentant `last_accessed`, `access_count`, le vecteur objectifs et le poids de contradiction comme le fait déjà `mcp_server.update_vitality`), persister la nouvelle valeur, puis appliquer `tier_for` pour décider archivage ou promotion. Cela rend les imports actuellement morts utiles et active le pilier "oubli".

### 2. Le jardin contrefactuel ne pousse et ne se vérifie jamais seul

Les descriptions des skills `dream-counterfactual` et `dream-search-pgt` annoncent un déclenchement automatique sur les nœuds tagués `type=error` ou `consensus_score<0.4`, et une vérification des branches à expiration. Aucune de ces deux actions n'existe dans `scheduler.py`. `propose_counterfactual` et `verify_counterfactual` ne tournent que sur invocation manuelle d'un skill. Le jardin reste donc statique en fonctionnement autonome.

Correctif : ajouter deux passes au cycle nocturne. Une passe de semis qui appelle `counterfactual_garden.generate_garden` sur les nœuds `type='error'` récents ou les clusters rejetés sous le seuil de consensus. Une passe de vérification qui appelle `verify_counterfactual` sur les branches dont `validity_to` est dépassé, pour promouvoir, faire décroître ou élaguer.

### 3. Aucune sonde Ollama, échecs silencieux

`consensus_router`, `counterfactual_garden` et `sanitize_local` postent vers `127.0.0.1:11434` sans vérifier qu'Ollama répond. Si Ollama est éteint pendant le cycle, `debate()` lève une exception, `run_cycle` l'attrape par cluster, incrémente `dream_cycle_failed_total{phase="debate"}` et continue. Le cycle se termine en statut "ok" avec zéro note acceptée, sans alerte claire. `health_check` ne sonde pas Ollama non plus.

Correctif : ajouter une fonction `ollama_up()` (un GET sur `http://127.0.0.1:11434/api/tags` avec timeout court). L'appeler en tête de `run_cycle` pour refuser le cycle proprement et logger en erreur si Ollama est absent, plutôt que d'enchaîner les échecs. L'exposer dans `health_check` et via une gauge `dream_ollama_up`.

## Écarts de préparation GitHub (priorité moyenne)

Le dossier n'est pas encore un dépôt git (`git status` échoue). Il faut `git init`, un premier commit, puis le tag `v0.2.2` conforme au workflow de release noté dans ta mémoire (bump des deux manifests, tag, patch du cache local).

Fichiers communautaires absents, à ajouter pour un dépôt public crédible, surtout vu la manipulation de secrets et de crypto :
- `SECURITY.md` (modèle de menace, surface réseau verrouillée sur 127.0.0.1, comment signaler une faille, gestion de la clé Ed25519).
- `CONTRIBUTING.md` (installer `requirements-dev.txt`, lancer `pytest`, conventions).
- `.github/ISSUE_TEMPLATE/` et un template de PR.

`plugin.json` gagnerait à déclarer `homepage` et `repository` une fois l'URL GitHub connue. Optionnel : `CODEOWNERS`, configuration Dependabot.

Le job CI installe `requirements-dev.txt`, donc lancedb, pyarrow et sentence-transformers (plusieurs centaines de Mo) à chaque push, alors que la suite tourne sans la stack ML. Scinder en un job léger (deps mcp, cryptography, prometheus-client, httpx, pytest, numpy) couvrant les tests actuels et un job lourd optionnel accélérerait la CI et réduirait sa fragilité.

## Écarts mineurs

Le README mentionne "18 regex" dans la section sécurité alors que `sanitize_local.PATTERNS` en compte 17. Aligner le chiffre.

Le CHANGELOG 0.2.0 liste "8 skills", la version actuelle en expose 10 (`dream-admin` et `dream-explain-node` ajoutés). Le tableau du README est correct (10), seul l'historique est à laisser tel quel.

Le README est en français, la description et les keywords des manifests en anglais. Choix assumable, à garder cohérent selon l'audience visée.

## Plan d'action ordonné

1. Brancher le recalcul de vitalité dans `run_cycle` (écart 1).
2. Ajouter les passes de semis et de vérification contrefactuelle au cycle (écart 2).
3. Ajouter la sonde `ollama_up()` et son refus de cycle propre (écart 3).
4. Corriger le compte de regex dans le README.
5. Ajouter `SECURITY.md`, `CONTRIBUTING.md`, templates issue et PR.
6. Renseigner `homepage` et `repository` dans `plugin.json`.
7. Scinder la CI en job léger et job lourd.
8. `git init`, premier commit, tag `v0.2.2`, push.
