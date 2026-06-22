"""Tests for the v0.6.0 reliability and architecture improvements.

Stdlib + light deps only: no Ollama, no sentence-transformers, no LanceDB, so
the suite stays fast and runs in CI. Each test encodes WHY the behaviour matters
(see the dream improvement notes), not just that a function returns something.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import dream_buffer
import dream_home
import doctor
import load_bearing
import node_store
import observability
import sanitize_local


# ── F2: the hook path redacts with regex only, never the LLM ─────────────────
class TestRegexOnlySanitise:
    def test_redacts_email_without_calling_the_llm(self, monkeypatch):
        def _boom(_text):
            raise AssertionError("sanitize_regex_only must not call the LLM")

        monkeypatch.setattr(sanitize_local, "_llm_pass", _boom)
        r = sanitize_local.sanitize_regex_only("write to john.doe@example.com now")
        assert r.model == "regex-only"
        assert "<EMAIL>" in r.text
        assert "john.doe@example.com" not in r.text

    def test_secret_is_redacted(self):
        r = sanitize_local.sanitize_regex_only("key sk-ant-" + "a" * 50)
        assert "<SECRET:anthropic_key>" in r.text


# ── F2: the buffer record advertises which sanitiser tier ran ────────────────
class TestBufferSanitiserFlag:
    def test_regex_flag_set_and_file_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dream_buffer, "BUFFER_DIR", tmp_path)
        rec = dream_buffer.append_event(
            {"type": "fact", "content": "Decision: migrate the store to X"}, full_llm=False
        )
        assert rec["meta"]["sanitised"] == "regex"
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8").strip()


# ── F1: the signals that turn a silent outage into a visible one ─────────────
class TestObservability:
    def _patch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "LOG_DIR", tmp_path)
        monkeypatch.setattr(observability, "BUFFER_DIR", tmp_path / "buffer")
        monkeypatch.setattr(observability, "HEARTBEAT_PATH", tmp_path / "heartbeat.jsonl")
        monkeypatch.setattr(observability, "LAST_CYCLE_PATH", tmp_path / "last_cycle.json")

    def test_beat_appends_one_line(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        observability.beat("stop", "ok", appended=2)
        lines = (tmp_path / "heartbeat.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["hook"] == "stop" and rec["status"] == "ok" and rec["appended"] == 2

    def test_cycle_status_roundtrip(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        observability.record_cycle("skipped_sparse", {"raw_events": 0})
        out = observability.read_cycle()
        assert out["status"] == "skipped_sparse"
        assert out["raw_events"] == 0
        assert "at" in out

    def test_last_buffer_write_reports_freshness(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        (tmp_path / "buffer").mkdir()
        (tmp_path / "buffer" / "2026-06-22.jsonl").write_text("{}\n", encoding="utf-8")
        iso = observability.last_buffer_write()
        assert iso is not None and "T" in iso


# ── F5: the load-bearing classifier ──────────────────────────────────────────
class TestLoadBearing:
    def test_lexical_hit(self):
        assert load_bearing.lexical_hit("Décision : on migre vers Postgres")
        assert not load_bearing.lexical_hit("comment vas-tu aujourd'hui")

    def test_is_candidate_length_floor(self):
        assert not load_bearing.is_candidate("decide")  # below the length floor
        assert load_bearing.is_candidate("Decision: we migrate the database to Postgres")

    def test_classify_without_llm_uses_lexical(self):
        assert load_bearing.classify("Convention: snake_case everywhere always", use_llm=False) is True
        assert load_bearing.classify("hello there my good friend", use_llm=False) is False


# ── F4: the cheap self-check and the dependency it was missing ───────────────
class TestDoctorQuick:
    def test_run_quick_returns_problem_strings(self):
        problems = doctor.run_quick()
        assert isinstance(problems, list)
        assert all(isinstance(p, str) for p in problems)

    def test_pandas_now_required(self):
        assert "pandas" in doctor.REQUIRED


# ── F3: the shared node write path exists and is callable ────────────────────
class TestNodeStore:
    def test_persist_node_is_callable(self):
        assert callable(node_store.persist_node)


# ── F7: DREAM_HOME resolves the runtime, not the repo ────────────────────────
class TestDreamHome:
    def test_env_takes_precedence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DREAM_HOME", str(tmp_path))
        assert dream_home.resolve() == tmp_path
