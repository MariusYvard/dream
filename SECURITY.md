# Politique de sécurité

## Modèle de menace

Dream traite des transcripts de session qui peuvent contenir des secrets (clés API, jetons, identifiants), des données personnelles et du contexte projet confidentiel. La conception part du principe que ces données ne doivent jamais quitter la machine.

Garanties en place :

- Surface réseau verrouillée. Le serveur FastMCP écoute sur `127.0.0.1`. L'endpoint Prometheus écoute sur `127.0.0.1:9464`. Les modèles tournent en local via Ollama sur `127.0.0.1:11434`. Aucun appel sortant.
- Sanitisation avant écriture disque. Tout évènement traverse `dream_buffer.append_event`, qui applique 17 motifs regex déterministes (clés Anthropic, OpenAI, GitHub, AWS, Stripe, clés privées PEM, webhooks Slack, JWT, jetons Bearer, IBAN, cartes, IP, téléphones, emails) puis un passage LLM local (`gemma4:e4b`). Si Ollama est absent, le pipeline retombe sur le passage regex seul, il ne laisse pas passer de texte brut.
- Intégrité du graphe. Chaque écriture est signée en Ed25519 et chaînée dans un arbre Merkle. La vérification tourne à chaque cycle nocturne et dans `health_check`.

## Données à ne jamais committer

Le `.gitignore` exclut `keys/`, `*.private`, `pgt.sqlite`, `vectors.lance/`, `buffer/`, `archive/`, `rejected/`, `circuit.json` et `CLAUDE.md`. La clé privée Ed25519 vit dans `keys/` avec permissions restreintes (`chmod 600` sur Linux et macOS, ACL restreinte sur Windows). Sa fuite casse l'intégrité du ledger pour cette machine. Ne la partage jamais.

## Signaler une vulnérabilité

Envoie un rapport privé à mariusyvard72@gmail.com avec le sujet "Dream security". Inclus une description, les étapes de reproduction et l'impact estimé. Ne crée pas d'issue publique pour une faille non corrigée.

Délai de première réponse visé : 72 heures.

## Versions supportées

Seule la dernière version mineure publiée reçoit des correctifs de sécurité.
