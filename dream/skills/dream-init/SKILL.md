---
name: dream-init
description: Initialise the cognitive dream architecture on a new machine. Use when the user asks "initialise dream", "installe le palais graphique", "setup dream cycle", "prepare la memoire dream", "bootstrap PGT", or when the dream/ data directory is missing. Provisions Ollama models, LanceDB store, SQLite metadata, NetworkX graph and the FastMCP server entrypoint.
---

# Dream Init

Provision the full cognitive dream stack on the host machine. Run only once per environment, or when the data layer must be rebuilt from scratch.

## Instructions for Claude

1. Read `references/stack-installation.md` for the exact model list, RAM envelope and runtime selection rules. Do not assume defaults.
2. Probe the host before installing.
   - Total RAM via `bash` (`grep MemTotal /proc/meminfo` on Linux, `sysctl hw.memsize` on macOS, `(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory` on Windows).
   - Available GPU via `nvidia-smi` or absence detection.
   - Ollama presence: `ollama --version`. If missing, propose the install command and wait for confirmation.
3. Pull the required models, in this order, stopping at the first failure:
   - `ollama pull gemma4:e4b` (sanitisation, 9.6 Go)
   - `ollama pull gemma4:26b` (consolidation MoE, 18 Go, 3.8B params actifs sur 25.2B totaux)
   - bge-m3 se telecharge automatiquement via sentence-transformers au premier import.
4. Build the data layout under `${DREAM_HOME:-$HOME/.dream}`:
   - `pgt.sqlite` (metadata, vitality, ledger pointers)
   - `vectors.lance/` (LanceDB embeddings)
   - `graph.gpickle` (NetworkX serialised graph)
   - `buffer/YYYY-MM-DD.jsonl` (transcripts du jour)
   - `archive/cold/` (oubli froid)
   - `keys/` (Ed25519 private + public)
5. Generate the Ed25519 ledger key pair via `scripts/ledger_sign.py --bootstrap`. Store the private key with `chmod 600` (Linux/macOS) or `icacls /inheritance:r /grant:r "%USERNAME%:F"` (Windows).
6. Run the schema migration: `python ${CLAUDE_PLUGIN_ROOT}/scripts/lancedb_init.py` then `python ${CLAUDE_PLUGIN_ROOT}/scripts/db_init.py`. Both are stdlib-friendly and idempotent; `db_init.py` applies `graph_schema.sql` through Python so no `sqlite3` CLI binary is required (it is absent by default on Windows). The MCP server and the scheduler also self-heal the schema on startup, so this step is belt-and-suspenders.
7. Start the FastMCP server in foreground for a smoke test: `python ${CLAUDE_PLUGIN_ROOT}/scripts/mcp_server.py --smoke`. Confirm the ten tools are listed (`store_event`, `search_semantic`, `query_relations`, `update_vitality`, `propose_counterfactual`, `sanitize_local`, `load_context`, `health_check`, `set_mode`, `verify_counterfactual`).

8. **Platform registration** — This step wires Dream into the host environment so it runs autonomously. The actions differ by OS.

   **Windows (run this first on Windows before step 9)**

   a. Detect the Python 3.12 executable. Try in order:
      - `py -3.12 -c "import sys; print(sys.executable)"`
      - Common paths: `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`, `C:\Python312\python.exe`
      - Do NOT use the bare `python` or `python3` command — on Windows these often resolve to a different version (e.g. 3.14) that lacks the dream deps, and failures are silent.

   b. Run the registration script (stdlib-only, safe before deps are installed):
      ```
      <python312> ${CLAUDE_PLUGIN_ROOT}/scripts/setup_windows.py --dream-home ${DREAM_HOME}
      ```
      This script performs four sub-steps atomically:
      - Injects `mcpServers.dream` into `%APPDATA%\Claude\claude_desktop_config.json` so the Dream MCP tools (`dream__store_event`, `dream__health_check`, etc.) are available in every Claude session.
      - Injects `SessionStart` and `Stop` hooks into `%USERPROFILE%\.claude\settings.json` using the detected Python 3.12 path, so `hook_stop.py` fires at session end and populates the nightly buffer automatically.
      - Creates a Windows Task Scheduler task `Dream\NightlyCycle` that fires `scheduler.py --once` at 02:05 every day.

   c. If the script exits non-zero, read its output carefully. Common causes:
      - Python 3.12 not found → install from https://www.python.org/downloads/
      - `%APPDATA%\Claude` missing → Claude Desktop not installed yet
      - `schtasks` access denied → re-run from an elevated PowerShell prompt

   d. After the script succeeds, **restart Claude Desktop** before proceeding to step 9. The MCP server starts on next launch.

   **Linux**

   Create a systemd user timer:
   ```
   systemctl --user enable --now dream-cycle.timer
   ```
   Unit files are in `${CLAUDE_PLUGIN_ROOT}/systemd/`. Copy them to `~/.config/systemd/user/` before enabling.

   **macOS**

   Create a launchd user agent:
   ```
   launchctl load ~/Library/LaunchAgents/com.dream.nightly.plist
   ```
   The plist template is in `${CLAUDE_PLUGIN_ROOT}/launchd/`.

9. Print a final report listing model sizes, RAM peak, disk usage and the public ledger key.

## Guardrails

If RAM <14 Go, refuse `gemma4:26b` and fall back to `gemma4:e4b` for consolidation. Log the downgrade in `pgt.sqlite::config` and warn the user. Le fallback `gemma4:e4b` reste superieur a Gemma 3 27B sur AIME 2026 (42.5% vs 20.8%) mais perd nettement sur LiveCodeBench (52% vs 80%).

Do not pull any model not listed in `references/stack-installation.md`. The model list is locked.

Never overwrite an existing `keys/ed25519.private` without explicit confirmation.

On Windows, never use the bare `python` command in any config file path — always resolve and write the full absolute path to the 3.12 executable. Partial-path entries cause silent failures that are hard to diagnose.

## Sortie utilisateur (FR)

A la fin, presenter au user:

- chemin du dossier `DREAM_HOME`,
- liste des modeles installes avec leur taille disque,
- RAM pic mesuree pendant le smoke test,
- empreinte SHA-256 de la cle publique,
- confirmation que l'enregistrement MCP + hooks est actif (Windows uniquement : rappeler de redemarrer Claude Desktop),
- commande pour lancer manuellement un cycle de reve: `python ${DREAM_HOME}/scripts/scheduler.py --once`.
