"""Model and embedding profile. One switch trades quality for footprint so the
stack runs on a normal machine, while every per-variable env override is kept.

    DREAM_PROFILE=full   (default)  bge-m3 + gemma4:12b/e4b, ~16 GB class
    DREAM_PROFILE=lite              bge-small-en + llama3.2:3b, ~6 GB class

Override any single model regardless of profile:
    DREAM_CONSOLIDATION_MODEL  DREAM_COUNTERFACTUAL_MODEL  DREAM_CLASSIFIER_MODEL
    DREAM_RETRIEVAL_MODEL      DREAM_SANITISE_MODEL        DREAM_EMBED_MODEL
    DREAM_EMBED_DIM

Note: the embedding dimension is baked into the LanceDB table at init, so pick
the profile (or DREAM_EMBED_MODEL/DIM) before the first run, not after.
"""
from __future__ import annotations

import os

_PRESETS = {
    # llm   = consolidation / counterfactual / retrieval (reasoning)
    # small = sanitisation / load-bearing classification (cheap calls)
    "full": {"llm": "gemma4:12b", "small": "gemma4:e4b", "embed_model": "BAAI/bge-m3", "embed_dim": 1024},
    "lite": {"llm": "gemma4:e4b", "small": "gemma4:e4b", "embed_model": "BAAI/bge-small-en-v1.5", "embed_dim": 384},
}


def name() -> str:
    p = os.environ.get("DREAM_PROFILE", "full").strip().lower()
    return p if p in _PRESETS else "full"


def _preset() -> dict:
    return _PRESETS[name()]


def consolidation_model() -> str:
    return os.environ.get("DREAM_CONSOLIDATION_MODEL", _preset()["llm"])


def counterfactual_model() -> str:
    return os.environ.get("DREAM_COUNTERFACTUAL_MODEL", _preset()["llm"])


def retrieval_model() -> str:
    return os.environ.get("DREAM_RETRIEVAL_MODEL", os.environ.get("DREAM_CONSOLIDATION_MODEL", _preset()["llm"]))


def classifier_model() -> str:
    return os.environ.get("DREAM_CLASSIFIER_MODEL", _preset()["small"])


def sanitise_model() -> str:
    return os.environ.get("DREAM_SANITISE_MODEL", _preset()["small"])


def embed_model() -> str:
    return os.environ.get("DREAM_EMBED_MODEL", _preset()["embed_model"])


def embed_dim() -> int:
    return int(os.environ.get("DREAM_EMBED_DIM", _preset()["embed_dim"]))
