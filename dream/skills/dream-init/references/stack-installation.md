# Stack Installation Reference

## Locked model list (do not deviate)

| Phase | Model | Disque | RAM active | Runtime | Licence |
|-------|-------|--------|-----------|---------|---------|
| Sanitisation | gemma4:e4b | 9.6 Go | ~5 Go | Ollama | Gemma Terms |
| Sanitisation (low RAM) | gemma4:e2b | 7.2 Go | ~3 Go | Ollama | Gemma Terms |
| Embedding (default) | bge-m3 | 2 Go | ~6 Go GPU / ~3 Go CPU | sentence-transformers | MIT |
| Embedding (high accuracy) | Qwen3-Embedding-8B | 5 Go | ~5 Go GPU | sentence-transformers | Apache 2.0 |
| Consolidation (MoE) | gemma4:26b | 18 Go | ~14 Go (3.8B actifs sur 25.2B totaux) | Ollama | Gemma Terms |
| Consolidation (fallback) | gemma4:e4b | 9.6 Go | ~5 Go | Ollama | Gemma Terms |
| Reranking | ms-marco-MiniLM-L-6-v2 | 90 Mo | <0.5 Go | transformers | Apache 2.0 |

## RAM envelope

Peak ceiling: 14.5 Go (laisse 1.5 Go de marge pour l'OS sur un poste 16 Go).

Concurrency rules:
- never load consolidation (`gemma4:26b`) + embedding heavyweight (`Qwen3-Embedding-8B`) in parallel,
- sanitisation (`gemma4:e4b`) runs in a daemon process kept warm,
- reranker is loaded on demand and unloaded after every batch,
- pendant le cycle nocturne, unload `gemma4:e4b` avant de pull `gemma4:26b` en VRAM/RAM si la marge passe sous 1.5 Go.

## Disk footprint

Approximate disk usage for the locked stack: 30 Go.
- gemma4:e4b ~9.6 Go,
- bge-m3 ~2 Go,
- gemma4:26b ~18 Go,
- ms-marco-MiniLM-L-6-v2 ~90 Mo,
- LanceDB + SQLite croissance estimee 500 Mo/mois pour 1k evenements/jour.

## RAM envelope detail

L'avantage `gemma4:26b` MoE: 25.2B parametres totaux mais seulement 3.8B actifs par token (8 experts actifs sur 128, plus 1 shared). En pratique sur Ollama avec Q4_K_M sous-jacent, le pic RAM est de l'ordre de 13-14 Go (les experts sont charges mais le compute pass n'en sollicite que 3.8B). Sampling recommande: `temperature=1.0, top_p=0.95, top_k=64`. Le mode "thinking" peut etre coupe en omettant le token `<|think|>` dans le system prompt, ce qui reduit la latence de 30 a 60% pour les phases ou le raisonnement chain-of-thought n'apporte rien (sanitisation, reranking).

## Network policy

Aucun appel sortant durant la sanitisation. Le FastMCP est `bind 127.0.0.1` par defaut. Toute exfiltration involontaire est bloquee par les regex de `sanitize_local.py`.
