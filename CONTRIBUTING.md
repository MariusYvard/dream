# Contribuer à Dream

Merci de ton intérêt. Ce document décrit comment installer l'environnement, lancer les tests et proposer un changement.

## Environnement de développement

Python 3.12 requis. Ollama nécessaire seulement pour exécuter le cycle de bout en bout, pas pour la suite de tests légère.

```bash
git clone https://github.com/MariusYvard/dream.git
cd dream
pip install -r requirements-dev.txt
```

## Lancer les tests

La suite tourne sans la stack ML ni Ollama. Le test smoke vérifie que les 10 outils MCP s'enregistrent via l'API publique `mcp.list_tools()`, sans charger sentence-transformers ni lancedb.

```bash
pytest -q tests/
```

Le job CI léger installe seulement `mcp`, `cryptography`, `prometheus-client`, `httpx`, `numpy` et `pytest`. Un changement qui casse cette suite ne sera pas fusionné.

## Conventions de code

Le code suit le style existant. Points non négociables :

- Imports lourds (sentence-transformers, lancedb, numpy, networkx) chargés en lazy dans les fonctions qui en ont besoin, jamais au niveau module d'un point d'entrée léger.
- Toute écriture qui touche le graphe passe par le ledger (`ledger_sign.append_leaf`).
- Tout évènement entrant passe par la sanitisation avant le disque. Ne contourne jamais `dream_buffer.append_event`.
- Les chemins de données viennent de `DREAM_HOME`, jamais en dur.

## Proposer un changement

1. Crée une branche depuis `main`.
2. Ajoute ou met à jour les tests qui encodent l'intention du changement, pas seulement le comportement.
3. Mets à jour le `CHANGELOG.md` (format Keep a Changelog).
4. Si tu modifies une capacité, ouvre une PR avec le template fourni.

## Versionnage et release

SemVer. Une release suit cette checklist :

1. Bump de `version` dans `.claude-plugin/plugin.json` et `.claude-plugin/marketplace.json` (les deux occurrences).
2. Entrée datée dans le `CHANGELOG.md`.
3. Tag git `vX.Y.Z` poussé sur le dépôt.
4. Patch du cache local du plugin si applicable.
