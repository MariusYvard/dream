# Examples

## quickstart.py

A self-contained end-to-end run in a throwaway `DREAM_HOME`: initialise the
stores, write a few memories into the Palais Graphique Temporel (vector +
SQLite + signed Ed25519 ledger + graph), build the PageIndex-style topic tree,
and assemble a session-context bundle.

```bash
pip install -r requirements.txt
python examples/quickstart.py
```

The first run loads the bge-m3 embedding model once (about a minute). A local
[Ollama](https://ollama.com) is optional: without it, `load_context` falls back
from reasoning-based retrieval to embedding ranking. The nightly consolidation
debate needs a local LLM (`gemma4:12b` by default) and is shown as a follow-up
command, not run by the demo:

```bash
python scripts/scheduler.py --once
```

Nothing the demo writes touches a real install: it runs entirely under a
temporary directory printed at startup.
