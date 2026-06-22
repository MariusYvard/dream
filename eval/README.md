# Eval

A small, reproducible signal that the memory layer actually retrieves what it
should, that you can run before and after a change.

## recall_eval.py

Stores a synthetic fact set into a throwaway PGT, then queries each fact by a
paraphrase and reports recall@k (is the right memory in the top k?). It runs the
real write + embedding + vector-search path, needs no LLM server, and finishes
in about a minute (the first run loads the embedding model once).

```bash
pip install -r requirements.txt
python eval/recall_eval.py
# lite embedder (bge-small) instead of bge-m3:
DREAM_PROFILE=lite python eval/recall_eval.py
```

Example output:

```
dataset: 20 fact/query pairs
profile: full
retrieval recall (embedding search, no rerank, no LLM):
  recall@1: ...
  recall@3: ...
  recall@5: ...
```

## consolidation_eval.py

Asks whether the nightly cycle *improves* the memory, not just whether retrieval
works. It seeds a day buffer where a newer decision supersedes an older one (the
database moved from MySQL to PostgreSQL) plus noise, runs the real dream cycle,
then checks that the top retrieved node sits closer to the current truth than to
the stale one (embedding similarity, robust to the debate paraphrasing), and
that the noise was dropped.

```bash
python eval/consolidation_eval.py             # needs a local Ollama (the debate)
python eval/consolidation_eval.py --selftest  # deterministic, no stack
```

It is non-deterministic (a multi-agent LLM debate) and skips cleanly when Ollama
is down. Treat the result as a signal, not a fixed score.

## Scope and honesty

The dataset is synthetic and small, with deliberate near-neighbour distractors
(many "Atlas uses X" facts) so retrieval has to discriminate. This is a sanity
and regression number, not a leaderboard claim against [Zep](https://github.com/getzep/graphiti)
or [mem0](https://github.com/mem0ai/mem0), which publish results on standard
benchmarks (LongMemEval, LoCoMo). To make this meaningful for your use case,
replace `dataset.jsonl` with your own `{ "fact": ..., "query": ... }` pairs. A
natural next step is to also score retrieval *after* a nightly consolidation, to
measure what consolidation adds.
