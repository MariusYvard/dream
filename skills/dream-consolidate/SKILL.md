---
name: dream-consolidate
description: Run the nightly cognitive consolidation cycle. Use when the user asks "lance le cycle de reve", "consolide la memoire", "run dream cycle", "consolide les notes du jour", "fusionne les transcripts", or when triggered automatically at 02:05 by the scheduler. Executes the 4-phase pipeline (Orientation, Signal Gathering, Consolidation, Garbage Collection) with multi-agent debate and vitality update.
---

# Dream Consolidate

Execute the full nightly consolidation cycle over the day buffer. The cycle has 4 phases and runs in a single transactional pass.

## Instructions for Claude

1. Verify pre-conditions:
   - dream daemon is `NORMAL` mode (see `dream-health`),
   - `${DREAM_HOME}/buffer/<today>.jsonl` exists,
   - last consolidation timestamp older than 18h.

2. Phase 1 - Orientation:
   - List `${DREAM_HOME}` content.
   - Read `${DREAM_HOME}/CLAUDE.md` (<500 tokens).
   - Compare current graph state against the day's transcripts. Flag `drifted_facts` (nodes whose `content` no longer matches transcript evidence).

3. Phase 2 - Signal Gathering:
   - Hybrid retrieval on the day buffer using BM25 + bge-m3.
   - Filter `load-bearing` events: explicit corrections, decisions, recurring patterns. Drop chit-chat with vitality projection <0.4.

4. Phase 3 - Consolidation + Anchoring:
   - For every cluster of load-bearing events, spawn the 4-role debate (`Archiviste`, `Sceptique`, `Optimiseur`, `Expert Domaine`). See `references/multi-agent-debate.md` for the prompts.
   - Aggregate the consensus score: `Score = 0.25*coherence + 0.30*consistency + 0.20*conciseness + 0.25*accuracy`.
   - Decisions:
     - `Score >= 0.7`: accept, write to thematic file under `topics/`, commit ledger entry.
     - `0.5 <= Score < 0.7`: emit a HITL webhook (Slack/email) with the debate trail, mark node `status=pending_hitl`.
     - `Score < 0.5`: reject, log full debate trail to `${DREAM_HOME}/rejected/<date>.jsonl`.
   - Convert every relative date to ISO 8601. Resolve contradictions by adding `supersedes` edges, never by deleting.

5. Phase 4 - Garbage Collection:
   - Recompute vitality for every touched node via `references/vitality-formula.md`.
   - Demote nodes with `V < 0.2` to cold archive (`archive/cold/`).
   - Promote nodes with `V > 0.85` to the Redis hot cache.
   - Rebuild `CLAUDE.md` index (<500 tokens) using only nodes with `V > 0.5` and at most 30 topics.

6. Commit:
   - Sign each new ledger entry with Ed25519 (`scripts/ledger_sign.py`).
   - Append the Merkle leaf, recompute the root, store the new root in `pgt.sqlite::ledger_state`.
   - Emit a `prometheus` counter increment for `dream_cycle_completed_total`.

## Idempotence

If the cycle is interrupted mid-phase, the next run must resume from the last completed phase. The state machine is persisted in `pgt.sqlite::cycle_state` after each phase commit.

## Refus

Refuser de tourner si:
- mode `SECURISE` actif,
- ledger Merkle integrity check echoue,
- moins de 3 evenements load-bearing dans le buffer (cycle inutile).

## Sortie utilisateur (FR)

Rapport synthetique, jamais plus de 10 lignes:

```
Cycle de reve 2026-05-16 02:05–02:14
- Phase 1: 4 drifted_facts detectes
- Phase 2: 38 evenements load-bearing retenus (sur 142)
- Phase 3: 27 acceptes, 4 HITL, 7 rejetes
- Phase 4: 12 demotions, 3 promotions, CLAUDE.md = 487 tokens
- RAM pic: 13.8 Go. Ledger root: 4a91...
```
