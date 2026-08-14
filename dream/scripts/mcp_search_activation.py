"""Hybrid search with spreading activation over the NetworkX graph.

The retrieval pipeline:
    1. BM25 + dense (LanceDB cosine) candidate set
    2. spreading_activation() over the graph from the top-k seeds
    3. cross-encoder rerank with ms-marco-MiniLM-L-6-v2
    4. linear combination into the final score
"""
from __future__ import annotations

import datetime as dt
import math
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lancedb
import networkx as nx
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
VECTORS_DIR = DREAM_HOME / "vectors.lance"
GRAPH_PATH = DREAM_HOME / "graph.gpickle"

_EMBEDDER: SentenceTransformer | None = None
_RERANKER: CrossEncoder | None = None
_GRAPH: nx.MultiDiGraph | None = None

RELATION_BONUS = {
    "supersedes": 1.2,
    "implements": 1.1,
    "depends_on": 1.0,
    "alternative_of": 0.9,
    "derived_from": 0.9,
    "contradicts": 0.5,
}


def embedder() -> SentenceTransformer:
    global _EMBEDDER
    if _EMBEDDER is None:
        import model_profile
        _EMBEDDER = SentenceTransformer(model_profile.embed_model())
    return _EMBEDDER


def reranker() -> CrossEncoder:
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _RERANKER


def graph() -> nx.MultiDiGraph:
    global _GRAPH
    if _GRAPH is None and GRAPH_PATH.exists():
        with GRAPH_PATH.open("rb") as fh:
            _GRAPH = pickle.load(fh)
    if _GRAPH is None:
        _GRAPH = nx.MultiDiGraph()
    return _GRAPH


def _temporal_recency(edge_from_iso: str) -> float:
    edge_from = dt.datetime.fromisoformat(edge_from_iso.replace("Z", "+00:00"))
    days = max(0.0, (dt.datetime.now(dt.timezone.utc) - edge_from).total_seconds() / 86400.0)
    return math.exp(-0.02 * days)


def spreading_activation(seed_nodes: list[str], max_depth: int = 3, decay: float = 0.7) -> dict[str, float]:
    g = graph()
    scores: dict[str, float] = {n: 1.0 for n in seed_nodes if n in g}
    frontier: list[tuple[str, float, int]] = [(n, 1.0, 0) for n in scores]
    head = 0
    while head < len(frontier):
        curr, score, depth = frontier[head]
        head += 1
        if depth >= max_depth:
            continue
        for _, neighbor, data in g.out_edges(curr, data=True):
            bonus = RELATION_BONUS.get(data.get("relation_type", ""), 1.0)
            recency = _temporal_recency(data.get("temporal_from", dt.datetime.now(dt.timezone.utc).isoformat()))
            edge_weight = data.get("weight", 0.8) * recency * bonus
            new_score = score * edge_weight * (decay ** depth)
            prev = scores.get(neighbor, -1.0)
            if new_score > prev:
                scores[neighbor] = new_score
                if g.nodes[neighbor].get("vitality", 0.0) > 0.3:
                    frontier.append((neighbor, new_score, depth + 1))
    return scores


@dataclass
class Hit:
    node_id: str
    final_score: float
    cosine: float
    bm25: float
    activation: float
    rerank: float
    content: str


def hybrid_search(query: str, k: int = 25, vitality_min: float = 0.3, rerank: bool = True) -> list[Hit]:
    db = lancedb.connect(str(VECTORS_DIR))
    table = db.open_table("nodes")
    vec = embedder().encode(query, normalize_embeddings=True)

    dense = table.search(vec).limit(k * 2).to_pandas()
    dense = dense[dense["vitality"] >= vitality_min].head(k * 2)

    corpus = dense["content"].tolist()
    if corpus:
        tokenised = [doc.lower().split() for doc in corpus]
        bm25 = BM25Okapi(tokenised)
        bm_scores = bm25.get_scores(query.lower().split())
        bm_norm = (bm_scores - bm_scores.min()) / (bm_scores.max() - bm_scores.min() + 1e-9)
    else:
        bm_norm = np.zeros(0)

    seeds = dense["id"].head(10).tolist()
    activation = spreading_activation(seeds, max_depth=3, decay=0.7)
    act_max = max(activation.values(), default=1.0)
    act_norm = {n: s / act_max for n, s in activation.items()}

    rows: list[Hit] = []
    for idx, row in dense.iterrows():
        nid = row["id"]
        cosine = float(1.0 - row.get("_distance", 0.0))
        bm = float(bm_norm[idx]) if idx < len(bm_norm) else 0.0
        act = act_norm.get(nid, 0.0)
        rows.append(Hit(node_id=nid, final_score=0.0, cosine=cosine, bm25=bm, activation=act, rerank=0.0, content=row["content"]))

    if rerank and rows:
        pairs = [(query, h.content) for h in rows]
        rk = reranker().predict(pairs).tolist()
        rk_arr = np.asarray(rk, dtype=float)
        rk_norm = (rk_arr - rk_arr.min()) / (rk_arr.max() - rk_arr.min() + 1e-9)
        for h, r in zip(rows, rk_norm.tolist()):
            h.rerank = float(r)

    for h in rows:
        h.final_score = 0.45 * h.rerank + 0.25 * h.cosine + 0.15 * h.bm25 + 0.15 * h.activation

    rows.sort(key=lambda h: h.final_score, reverse=True)
    return [h for h in rows if h.final_score >= 0.55][:k]
