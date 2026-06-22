"""Tests for the PageIndex-inspired topic tree and reasoning retrieval (v0.7.0).

topic_tree and reasoning_retrieval only, both light (stdlib + httpx), so this
runs in CI-light. No Ollama: the LLM call is monkeypatched or short-circuited.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import reasoning_retrieval
import topic_tree


def _seed(tmp_path):
    (tmp_path / "decision.md").write_text(
        "\n- 2026-06-22T10:00:00+00:00 :: Migrate the store to Postgres\n"
        "- 2026-06-22T11:00:00+00:00 :: Adopt snake_case everywhere\n",
        encoding="utf-8",
    )
    (tmp_path / "_tree.json").write_text("{}", encoding="utf-8")  # underscore file must be ignored


class TestTopicTree:
    def test_build_from_topics(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        monkeypatch.setattr(topic_tree, "TOPICS_DIR", tmp_path)
        tree = topic_tree.build_tree()
        assert tree["title"] == "Dream Memory"
        assert len(tree["children"]) == 1
        dec = tree["children"][0]
        assert dec["node_id"] == "decision" and len(dec["children"]) == 2
        assert dec["children"][0]["node_id"] == "decision-0"
        assert "Postgres" in dec["children"][0]["summary"]

    def test_underscore_files_skipped(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        monkeypatch.setattr(topic_tree, "TOPICS_DIR", tmp_path)
        ids = [c["node_id"] for c in topic_tree.build_tree()["children"]]
        assert "_tree" not in ids

    def test_render_and_collect(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        monkeypatch.setattr(topic_tree, "TOPICS_DIR", tmp_path)
        tree = topic_tree.build_tree()
        outline = topic_tree.render_for_llm(tree)
        assert "[decision-0]" in outline and "[decision]" in outline
        assert topic_tree.collect(tree, ["decision-1"])[0]["node_id"] == "decision-1"
        assert len(topic_tree.collect(tree, ["decision"])) == 2  # type node pulls all leaves

    def test_index_md(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        monkeypatch.setattr(topic_tree, "TOPICS_DIR", tmp_path)
        md = topic_tree.render_index_md(topic_tree.build_tree())
        assert md.startswith("# Dream Index") and "## decision" in md


class TestReasoningRetrieval:
    def test_empty_tree_short_circuits_without_ollama(self):
        out = reasoning_retrieval.select("any goal", {"title": "x", "children": []})
        assert out is not None and out["selected"] == [] and out["mode"] == "reasoning"

    def test_ollama_failure_returns_none(self, monkeypatch):
        class _Boom:
            def __init__(self, *a, **k):
                ...

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                raise RuntimeError("ollama down")

        monkeypatch.setattr(reasoning_retrieval.httpx, "Client", _Boom)
        tree = {"title": "x", "children": [{"node_id": "t", "title": "t", "summary": "s",
                "children": [{"node_id": "t-0", "title": "a", "summary": "aaaa"}]}]}
        assert reasoning_retrieval.select("goal", tree) is None

    def test_parses_selection(self, monkeypatch):
        class _Resp:
            def raise_for_status(self):
                ...

            def json(self):
                return {"response": '{"selected": ["decision-0"], "rationale": "matches goal"}'}

        class _Client:
            def __init__(self, *a, **k):
                ...

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(reasoning_retrieval.httpx, "Client", _Client)
        tree = {"title": "x", "children": [{"node_id": "decision", "title": "decision", "summary": "s",
                "children": [{"node_id": "decision-0", "title": "a", "summary": "aaaa"}]}]}
        out = reasoning_retrieval.select("goal", tree)
        assert out["selected"] == ["decision-0"] and "matches" in out["rationale"]
