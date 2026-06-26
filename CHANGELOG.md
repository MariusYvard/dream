# Changelog

All notable changes to the Dream plugin. Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [0.9.0] — 2026-06-26

Cross-project recall: memories now carry the project they came from, and `load_context` returns nodes, not just topics.

### Added
- **`project` tag on every node** — a nullable `project` column on `nodes` plus an idempotent `db_init` migration (a `PRAGMA`-guarded `ALTER TABLE` and `idx_nodes_project`) so a pre-existing `pgt.sqlite` gains the column without a rebuild. `store_event` reads `payload.project`, `node_store.persist_node` takes `project=` (and now `access_policy=`), the daily buffer record carries it end to end, and the nightly cycle attributes a consolidated node to its source project when the cluster is unanimous. `search_semantic` hits now include `project`.
- **Node-aware `load_context`** — the bundle gained a `memories` list (top base nodes whose vector aligns with the active goal, each labelled with its `project`) and a `projects` summary, trimmed to the token budget. Until now recall only surfaced topic files and `type='decision'` rows, so plain fact, process and person memories (and anything tagged by project) stayed invisible. This is what lets a session pull relevant context from across projects instead of a single mounted folder.

### Notes
- Buffer-to-node promotion during consolidation already existed (`node_store.persist_node`); it now propagates `project`. The session-capture hook still does not fire under Cowork, so the reliable write path remains an explicit `store_event`.

---

## [0.8.0] — 2026-06-22

Adoption pass: lower the install barrier, say what this is, and prove retrieval works.

### Added
- **Lite profile for low-footprint installs** (`scripts/model_profile.py`) — `DREAM_PROFILE=full|lite` selects the model and embedder stack from one switch, with every `DREAM_*_MODEL` / `DREAM_EMBED_MODEL` / `DREAM_EMBED_DIM` env override preserved. `lite` runs a single small model (`gemma4:e4b`) and the `bge-small-en` embedder (384-dim), roughly a 6 GB class instead of 16 GB. Wired through consolidation, counterfactual, retrieval, classification, sanitisation, the embedder and the LanceDB vector dimension, plus `setup_windows.py` so a lite server is `set DREAM_PROFILE=lite & python setup_windows.py`. Full-mode behaviour is unchanged. (The module is named `model_profile`, not `profile`, to avoid shadowing the Python stdlib `profile` that `transformers` imports during load.)
- **English positioning header in the README** — a one-line pitch, the capture → consolidate → recall loop diagram, a one-command quickstart (full and lite), and an honest comparison with mem0, Letta and Zep that credits each and frames Dream's niche (local-only, sleep-cycle consolidation, active forgetting). The detailed French guide follows it.
- **Reproducible retrieval eval** (`eval/recall_eval.py`, `eval/dataset.jsonl`) — stores a synthetic fact set with near-neighbour distractors and reports recall@k over the real write + embedding + vector-search path, no LLM server required. Baseline on the bundled 20-pair set (full profile, bge-m3): recall@1 90%, recall@3 95%, recall@5 100%. Honest scope: a sanity and regression signal, not a leaderboard claim.

---

## [0.7.1] — 2026-06-22

### Added
- `examples/quickstart.py` and `examples/README.md` — a self-contained end-to-end run in a throwaway `DREAM_HOME`: initialise the stores, write memories into the PGT (vector + SQLite + signed Ed25519 ledger + graph), build the topic tree, and assemble a session-context bundle. Runs without a local LLM (`load_context` falls back from reasoning to embedding ranking, the nightly debate is shown as a follow-up command). Lowers the "try it" barrier from a full plugin install to one command.

### Fixed
- `counterfactual_garden.py` default model was the retired `gemma4:26b` (the same class of bug fixed in `consensus_router` for 0.6.0). Corrected to `gemma4:12b` so a fresh install's first counterfactual pass does not fail on a missing model.

---

## [0.7.0] — 2026-06-22

### Added
- **Topic tree, a PageIndex-style table of contents over memory** (`scripts/topic_tree.py`) — the flat `topics/*.md` are folded into a hierarchical tree of summaries (`topics/_tree.json`), rebuilt each cycle. `_rebuild_claude_md` now renders that tree as a hierarchical `CLAUDE.md` index instead of a flat top-30 list, falling back to the node query when the tree is empty.
- **Vectorless, reasoning-based retrieval** (`scripts/reasoning_retrieval.py`) — `load_context` now tries a PageIndex-inspired path first: a local LLM reasons over the topic tree and picks the relevant nodes by relevance, not embedding cosine. This keeps the bge-m3 embedder off the `load_context` hot path (its cold load was the `-32001` cause) and degrades to the embedding ranking when the model is unreachable.
- **Retrieval traceability** — the bundle now carries `retrieval_mode` (`reasoning` or `embedding`), `retrieval_rationale` (the model's one-line justification) and `selected_node_ids`, so the warm-up context is explainable instead of opaque hybrid scores.
- `tests/test_pageindex.py` — 7 tests covering the tree build, the LLM outline and node collection, the markdown index, and the reasoning selector (happy path, empty tree, Ollama-down fallback).

### Changed
- `load_context.build_bundle` is split into `_build_reasoning` (vectorless, tried first) and `_build_embedding` (the previous path, now the fallback), sharing one SQLite extras query. The change is additive: consumers reading `claude_md` / `topics` are unaffected.

> Idea credit: the tree-of-summaries and reasoning-over-index approach is inspired by [PageIndex](https://github.com/VectifyAI/PageIndex) (MIT). No code was copied; this is a from-scratch adaptation to the PGT, kept as a complement to the graph and vector search, not a replacement.

---

## [0.6.0] — 2026-06-22

### Added
- **Observability that makes a silent outage impossible** (`scripts/observability.py`) — a heartbeat line is written to `logs/heartbeat.jsonl` on every hook run (including the no-op paths), every nightly cycle records its outcome to `logs/last_cycle.json` (including the skipped paths that previously returned before any state was written), and `health_check` now reports `last_buffer_write`, `last_cycle_status` and `last_cycle_at`. A 16-day capture outage went unnoticed because the Stop hook logged nothing to disk and `health_check` reported only mode and vitality; that signal now surfaces on day one.
- **Consolidation grows the graph** (`scripts/node_store.py`) — the nightly `accept` path now materialises each consolidated cluster as a base node (vector + SQLite + graph + ledger) through one shared `persist_node` write path, not just a `topics/*.md` line. Before this, only `store_event` ever created nodes, so `search_semantic`, `query_relations` and the `CLAUDE.md` index never reflected nightly consolidation.
- **LLM load-bearing classifier** (`scripts/load_bearing.py`) — the "is this worth remembering" decision is now a local-LLM judgment (`gemma4:e4b`) run during the cycle, language-agnostic, with a lexical fast-path, a length floor and a content-hash cache. It degrades to the lexical check when Ollama is unreachable, so the cycle never loses events to a model hiccup. The Stop hook keeps a cheap recall-oriented lexical pre-filter.
- **SessionStart self-check** — `hook_session_start.py` runs the cheap `doctor.run_quick()` checks (no ML import) at every session start and surfaces registration drift (dead paths, missing deps) to the log and a heartbeat, the exact failure mode that left a dead nightly-task path unnoticed for days.
- **Weekly backup** (`scripts/backup.py`) — snapshots the irreplaceable state (the SQLite graph through the online backup API, the Ed25519 keys, `topics/`, `graph.gpickle`, `circuit.json`) into `DREAM_HOME/backups/<stamp>/`, keeping the last N. `setup_windows.py` registers a `Dream\WeeklyBackup` task (Sundays 03:00) via a `backup.cmd` wrapper. The graph was wiped once with no recovery path; now there is one.
- **`scripts/dream_home.py`** — single resolver (`$DREAM_HOME` → `claude_desktop_config.json` → `~/.dream`) so skills and ad-hoc scripts target the deployed runtime, not the source repo. The `dream-consolidate` and `dream-load-context` skills now point at it.
- `tests/test_improvements.py` — 13 tests covering the regex-only sanitiser, the buffer sanitiser flag, the observability signals, the classifier, the doctor quick-check and DREAM_HOME resolution.

### Changed
- **Sanitisation moved off the Stop hook critical path** — the hook now writes the buffer with deterministic regex-only redaction (`sanitize_regex_only`, no LLM, no network) and tags the record `meta.sanitised = "regex"`. The nightly cycle upgrades those events with the full `gemma4:e4b` pass (`scheduler._upgrade_sanitisation`) before they reach topics or the graph. Each load-bearing line previously cost a ~30 s LLM call inside the hook, so a session with a handful of them blew the 60 s hook timeout and capture failed mid-way.
- **`load_context` caches topic embeddings** — topics are embedded against a per-content-hash cache (`topics/.embcache.json`); unchanged topics are never re-encoded. Re-embedding every topic on every call was part of the cold-path cost behind the `load_context` timeouts.

### Fixed
- **`search_semantic` crashed with `No module named 'pandas'`** — `mcp_search_activation.hybrid_search` calls `.to_pandas()` on the LanceDB result, but `pandas` was never declared. Added to `requirements.txt` and to `doctor.REQUIRED` (doctor previously reported all-green while search was broken).
- **`consensus_router` default model was the retired `gemma4:26b`** — corrected to `gemma4:12b`, matching `config/mcp_servers.json` and `setup_windows.py`. The env-var override masked it at runtime, but a default-path invocation would have failed.

---

## [0.5.0] — 2026-06-06

### Fixed
- **MCP -32001 timeouts on the first model-backed call (`store_event`, `search_semantic`, `load_context`)** — the ~80 s lazy sentence-transformers import plus the bge-m3 load happened inside the first tool call, past the ~30 s client timeout; the call could then complete server-side minutes later, reading as a failed-but-landed write. `_bootstrap` now preloads the embedder in its daemon thread after schema and metrics, so the handshake stays non-blocking and the first tool call answers in time. Opt out with `DREAM_PRELOAD_EMBEDDER=0`.
- **SECURISE dead-lock on an empty graph** — a freshly initialised PGT reports `vitality_avg = 0.0`, which tripped the breaker into SECURISE; consolidation refuses to run in SECURISE, so nothing could ever populate the graph, and manual `set_mode NORMAL` was overwritten by the next health probe. `HealthProbe` now carries `active_nodes` and the vitality trigger only applies when at least one active node exists. `health_check` exposes `active_nodes`. Two regression tests added.
- **Nightly task broken by plugin cache garbage collection** — registrations pointed into the plugin manager's versioned cache; a plugin update deleted the referenced `scheduler.py` ("can't open file ... No such file or directory" in `nightly.log`) and the 02:05 task failed every night. `setup_windows.py` now deploys a stable copy of the scripts to `DREAM_HOME/scripts` and registers the MCP server, the hooks and the task against that copy, through a `nightly.cmd` wrapper (schtasks /TR is capped at 261 characters).
- `setup_windows.py` — dependency verification timeout raised from 20 s to 300 s (the probe import alone takes ~80 s cold); `/RL HIGHEST` dropped from task creation (fails without an elevated shell); literal `\n` printed by the step 4 banner (raw string bug); stdout/stderr forced to UTF-8 so cp1252 consoles no longer crash on Unicode output.

### Changed
- Default consolidation and counterfactual model: `gemma4:26b` → `gemma4:12b` (26b retired on 2026-06-05: 17 GB pull, no fit in 16 GB RAM). Updated in `config/mcp_servers.json`, `setup_windows.py` and the README (model table, pull command, env var defaults).
- `.gitignore` — runtime `logs/` excluded.

### Added
- README section "Dépannage Windows (terrain)" — field-tested traps and their correct reading: -32001 semantics with idempotent uuid retry, plugin-cache path death, broken PATH/PATHEXT in spawned shells, filesystem tools misreporting `DREAM_HOME` as empty, OneDrive sandbox truncation, Ollama 412 on `gemma4:12b`, RAM margin, swallowed stdout.
- README note on the circuit breaker bootstrap exemption.

---

## [0.4.0] — 2026-06-03

### Fixed
- **MCP handshake timeout ("Could not attach to MCP server dream")** — on a cold start under load the server took 46 to 63 seconds to answer the `initialize` request, past the 60 second host timeout. Root cause: a deployed `mcp_server.py` imported the full ML stack (`sentence-transformers`, `lancedb`) at module load via `from mcp_search_activation import embedder, hybrid_search, spreading_activation`. Those imports are now lazy (inside the tools that use them), bringing cold import from 45 plus seconds down to about 8 seconds.

### Added
- **Background bootstrap** — schema creation and the Prometheus endpoint now run in a daemon thread (`_bootstrap`) so `mcp.run()` reaches its stdio loop immediately and the `initialize` handshake is never blocked by startup work. Tools that touch SQLite wait on `_SCHEMA_READY` (30 s) before their first query.
- **SQLite concurrency guard** — `_conn()` sets `busy_timeout = 30000` and `journal_mode = WAL` so two dream instances sharing one `DREAM_HOME` (the desktop app and the plugin runtime) wait for the write lock instead of stalling the cold start.
- **Dependency import guard** — a missing dependency now prints the interpreter path and the exact `pip install -r requirements.txt` command to stderr (visible in the host log) instead of a bare `ModuleNotFoundError`.
- `scripts/doctor.py` (and `mcp_server.py --doctor`) — a stdlib-only self-diagnostic that checks the interpreter, dependencies, `DREAM_HOME`, the SQLite schema, the metrics port, the `claude_desktop_config.json` registration and the cold import time.

### Changed
- `setup_windows.py` registers the server from the actual scripts directory (`SCRIPTS_DIR`) instead of a non-existent `DREAM_HOME/scripts` path, widens Python detection to 3.11 through 3.13 with a fallback to the running interpreter, and points the dependency hint at `requirements.txt` (was the wrong `fastmcp` package).

---

## [0.3.0] — 2026-05-29

### Added
- **Autonomous vitality decay** — `scheduler.run_cycle` now recomputes vitality for every active base node each night via `_recompute_vitality()`, feeding `vitality_engine.compute` with live `last_accessed`, `access_count`, goals vector and contradiction weight. Previously the vitality column only moved on `update_vitality` calls, so old unused nodes never cooled down and never reached the cold tier. Automatic forgetting now actually fires. This makes the formerly dead `VitalityInputs` / `vitality_compute` / `embedder` imports load-bearing.
- **Autonomous Counterfactual Garden** — the nightly cycle now seeds branches on recent `type='error'` nodes that have none yet (`_counterfactual_pass`) and verifies branches whose horizon has elapsed (`_verify_expired_branches`, promote/decay/prune). The garden was previously inert outside manual skill invocation.
- `scripts/ollama_health.py` — `ollama_up()` reachability probe (GET `/api/tags`, never raises). `DREAM_OLLAMA_URL` env var to relocate the endpoint.
- `prometheus_metrics.OLLAMA_UP` gauge (`dream_ollama_up`); surfaced in `health_check` as `ollama_up`.
- `SECURITY.md`, `CONTRIBUTING.md`, GitHub issue templates (bug, feature) and a pull request template.
- `homepage` / `repository` fields in `plugin.json`.

### Fixed
- **Silent cycle on Ollama down** — `run_cycle` probes Ollama up front and returns `skipped_ollama_down` (incrementing `dream_cycle_failed_total{phase="ollama"}` and setting the gauge to 0) instead of churning through per-cluster exceptions and finishing "ok" with zero consolidation.
- **README regex count** — corrected from 18 to 17 to match `sanitize_local.PATTERNS`.
- The nightly tier/archive pass now filters on `status = 'active'` so already-archived nodes are not rescanned.

### Changed
- CI split into a fast `test-light` job (light deps, every push and PR) and a `test-full` job with the ML stack (pushes only), so lancedb and sentence-transformers are no longer installed on every PR commit.

---

## [0.2.2] — 2026-05-29

### Added
- `LICENSE` — MIT license text (the manifest declared MIT but no file shipped).
- `scripts/db_init.py` — Python schema applier (`conn.executescript`) replacing the `sqlite3` CLI dependency, which is absent by default on Windows.
- `scripts/interpreter.py` — stdlib resolver that finds a deps-capable Python; the hooks re-exec through it when launched with a bare `python`.
- `.github/workflows/ci.yml` and `requirements-dev.txt` — pytest runs on every push/PR on Python 3.12.
- `pytest.ini` — test discovery configuration.
- `prometheus_metrics.record_search_latency()` / `record_sanitise_latency()` helpers; `psutil` added to `requirements.txt` for RAM measurement.

### Fixed
- **Prometheus port conflict** — `serve()` no longer raises when port 9464 is already bound (the MCP server holds it while the scheduler fires). It catches `OSError`, honours `DREAM_METRICS_PORT` / `DREAM_METRICS_ENABLED`, and returns a bool. The nightly cycle no longer dies on startup when Claude Desktop is open.
- **Schema never applied on Windows** — `db_init.py` is now run by the three setup scripts and self-healed on startup by both `mcp_server.py` and `scheduler.py`. A fresh host no longer fails with "no such table: nodes".
- **Circuit breaker inputs were dead** — the scheduler now writes `metric:consensus_rate_24h` and `metric:ram_peak_mb`, `health_check` measures live RAM, and `metric:latency_p95` is fed from search latency. CONSERVATEUR and the RAM SECURISE trigger can now actually fire.
- **Hollow observability** — `RAM_PEAK`, `VITALITY_AVG`, `HITL_PENDING`, `CIRCUIT_MODE`, `LEDGER_OK`, `SEARCH_LATENCY`, `SANITISE_LATENCY` gauges/histograms are now populated.
- **Debate ran without graph context** — `scheduler.run_cycle` passes real same-type high-vitality neighbours to `debate()` instead of an empty list, so the Sceptique and Expert roles can detect contradictions.
- **`store_event` write ordering** — vector lands in LanceDB before the SQLite metadata is committed, so a failed vector write never leaves orphan metadata.
- **Interpreter mismatch between install paths** — `hooks.json` (bare `python`) now self-corrects via the re-exec shim, matching the absolute-path manual setup.

### Changed
- `mcp_server.py` — heavy modules (sentence-transformers, lancedb, numpy, networkx, load_context, counterfactual_garden) are imported lazily inside their tools; `--smoke` lists tools via the public `mcp.list_tools()` API instead of the private `_tool_manager._tools`.
- `README.md` / `skills/dream-init` — corrected skill count (10) and tool count (10); documented the new metrics env vars.

### Added (carried from prior unreleased work)
- `.gitignore` covering `__pycache__`, `keys/`, `*.private`, `pgt.sqlite`, `vectors.lance/`, `buffer/`, `archive/`, `rejected/`, `circuit.json`, `CLAUDE.md`.
- `systemd/dream-cycle.service` and `systemd/dream-cycle.timer` for Linux autonomous scheduling.
- `launchd/com.dream.nightly.plist` for macOS autonomous scheduling.
- `scripts/setup_linux.py` and `scripts/setup_macos.py` — platform registration parity with `setup_windows.py`.
- `tests/` — pytest suite covering `vitality_engine`, `ledger_sign`, `circuit_breaker`, `hook_stop` and a `mcp_server` smoke test.
- `circuit_breaker.force_mode()` public function replacing the previous direct `_save()` call.

### Changed (carried from prior unreleased work)
- `hook_stop.py` — load-bearing detection normalises Unicode (NFD → ASCII fold) before matching.
- `scheduler.py` — `_cluster_events()` uses Jaccard single-linkage clustering within each type bucket (threshold 0.25).
- `mcp_server.py` — `store_event` updates `graph.gpickle` atomically (tmp file + rename); `set_mode` uses `circuit_breaker.force_mode()`.
- `config/mcp_servers.json` — `_comment` block explaining `${CLAUDE_PLUGIN_ROOT}` is a plugin-manager variable.
- `requirements.txt` — upper-bound pins on all dependencies.

---

## [0.2.0] — 2025-11-01

### Added
- Palais Graphique Temporel 2.0 with three-tier storage (SQLite + LanceDB + NetworkX).
- Ed25519 ledger with chained Merkle root for write integrity.
- Four-role multi-agent debate engine (`consensus_router.py`).
- Counterfactual Garden (`counterfactual_garden.py`) with verify/promote/decay/prune lifecycle.
- Three-mode circuit breaker (NORMAL / CONSERVATEUR / SÉCURISÉ) with hysteresis.
- Hybrid search: BM25 + dense (bge-m3) + spreading activation + cross-encoder rerank.
- Local sanitisation pipeline: 17 regex patterns + `gemma4:e4b` LLM pass.
- Prometheus metrics endpoint on `127.0.0.1:9464`.
- `scripts/setup_windows.py` for one-command Windows registration.
- 8 skills: `dream-init`, `dream-store-event`, `dream-search-pgt`, `dream-consolidate`, `dream-counterfactual`, `dream-sanitize`, `dream-health`, `dream-load-context`.
- APScheduler-based nightly daemon (02:05 local time).
- Redis hot cache with in-memory fallback.

---

## [0.1.0] — 2025-09-15

### Added
- Initial prototype: flat JSONL buffer, nightly summarisation via Ollama, basic CLAUDE.md injection.
- SessionStart and Stop hooks.
