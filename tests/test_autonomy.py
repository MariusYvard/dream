"""Tests for the autonomy refit: provider routing, catch-up, transcript scan."""
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="dream_auto_test_")
os.environ["DREAM_HOME"] = _TMP

sys.path.insert(0, str(Path(__file__).parent.parent / "dream" / "scripts"))

import dream_buffer  # noqa: E402
import llm  # noqa: E402
import session_scan  # noqa: E402


class TestProviderRouting:
    def test_module_self_check(self):
        """Routing, the local-only pin and lenient JSON parsing, in one go."""
        llm.demo()

    def test_reasoning_defaults_to_claude_once_the_cli_is_allowed(self, monkeypatch):
        monkeypatch.setenv("DREAM_ALLOW_CLI", "1")
        assert llm.provider_for("consolidation") == "claude"
        assert llm.provider_for("counterfactual") == "claude"

    def test_sanitisation_cannot_be_moved_off_the_machine(self):
        """Its input is raw text that has not been redacted yet. No env var,
        including the global one, may route it to a remote provider."""
        os.environ["DREAM_LLM_PROVIDER"] = "claude"
        os.environ["DREAM_PROVIDER_SANITISE"] = "claude"
        try:
            assert llm.provider_for("sanitise") == "ollama"
        finally:
            del os.environ["DREAM_LLM_PROVIDER"], os.environ["DREAM_PROVIDER_SANITISE"]

    def test_unknown_provider_falls_back_to_local(self):
        os.environ["DREAM_LLM_PROVIDER"] = "gpt5"
        try:
            assert llm.provider_for("consolidation") == "ollama"
        finally:
            del os.environ["DREAM_LLM_PROVIDER"]


class TestNeverSpawnClaudeInsideClaude:
    """Regression suite for the crash that took down Claude Desktop and the
    VS Code extension on every restart (exit code 4294967295).

    Chain: the SessionStart hook runs on every Claude session -> load_context ->
    reasoning_retrieval -> the retrieval role -> `claude -p` -> a ~300 MB
    process that outlives the hook Claude Code cancels at 10 s. Repeat per
    session on a machine with ~1.3 GB free and the host dies.
    """

    def test_cli_is_opt_in_not_opt_out(self, monkeypatch):
        monkeypatch.delenv("DREAM_ALLOW_CLI", raising=False)
        assert llm.cli_allowed() is False
        assert llm.provider_for("consolidation") == "ollama"
        assert llm.available("claude") is False

    def test_explicit_override_cannot_force_a_subprocess(self, monkeypatch):
        """Even DREAM_LLM_PROVIDER=claude must not spawn one without the opt-in:
        a stale env var must not be able to bring the machine down."""
        monkeypatch.delenv("DREAM_ALLOW_CLI", raising=False)
        monkeypatch.setenv("DREAM_LLM_PROVIDER", "claude")
        for role in ("consolidation", "counterfactual", "retrieval", "classifier"):
            assert llm.provider_for(role) == "ollama", role

    def test_retrieval_stays_local_even_when_allowed(self, monkeypatch):
        """Retrieval is what the hook calls. It is local unconditionally."""
        monkeypatch.setenv("DREAM_ALLOW_CLI", "1")
        assert llm.provider_for("consolidation") == "claude"
        assert llm.provider_for("retrieval") == "ollama"

    def test_fallback_never_reaches_for_the_cli(self, monkeypatch):
        """The ollama -> claude fallback must not smuggle a subprocess in."""
        monkeypatch.delenv("DREAM_ALLOW_CLI", raising=False)
        monkeypatch.setattr(llm, "_ask_claude", lambda *a, **k: pytest.fail("spawned a CLI"))
        monkeypatch.setattr(llm, "_ask_ollama", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        with pytest.raises(llm.LLMError):
            llm.ask("consolidation", "sys", "prompt")

    def test_the_hook_pins_both_guards_before_importing(self):
        """The hook must set the guards itself, not inherit them: it is the one
        entry point that always runs inside a Claude session."""
        hook = (Path(__file__).parent.parent / "dream" / "scripts" / "hook_session_start.py").read_text(encoding="utf-8")
        body = hook.split("# ── Logging setup")[0]
        assert 'os.environ["DREAM_LIGHT_CONTEXT"] = "1"' in body
        assert 'os.environ["DREAM_ALLOW_CLI"] = "0"' in body

    def test_session_start_path_imports_nothing_expensive(self):
        """The 10 s budget is spent on imports before any code runs, so the
        modules the hook touches must not pull in redis, numpy, torch or
        sentence-transformers at module level. Measured before the fix: 12.4 s,
        of which 7.3 s was cache_layer probing a Redis that does not exist."""
        import subprocess

        scripts = Path(__file__).parent.parent / "dream" / "scripts"
        code = (
            "import sys, json;"
            f"sys.path.insert(0, r'{scripts}');"
            "import cache_layer, load_context, doctor;"
            "heavy = [m for m in ('redis','numpy','torch','sentence_transformers','lancedb')"
            " if m in sys.modules];"
            "print(json.dumps(heavy))"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-400:]
        loaded = json.loads(out.stdout.strip().splitlines()[-1])
        assert loaded == [], f"session-start path imported {loaded}"

    def test_redis_is_opt_in(self, monkeypatch):
        """Probing a Redis nobody runs cost 7.3 s per session start."""
        import cache_layer

        assert cache_layer.backend() == "memory"

    def test_light_context_loads_no_model(self, monkeypatch, tmp_path):
        """The session-start bundle must come from files alone. Reaching the
        embedder here means loading 2.3 GB while the host has ~1.3 GB free."""
        import load_context

        monkeypatch.setenv("DREAM_LIGHT_CONTEXT", "1")
        monkeypatch.setattr(load_context, "TOPICS_DIR", tmp_path)
        monkeypatch.setattr(load_context, "CLAUDE_MD", tmp_path / "CLAUDE.md")
        monkeypatch.setattr(load_context.cache_layer, "get", lambda k: None)
        monkeypatch.setattr(load_context.cache_layer, "set", lambda k, v, ttl=None: None)
        monkeypatch.setattr(
            load_context, "embedder",
            lambda: pytest.fail("light mode must never touch the embedder"),
        )
        (tmp_path / "CLAUDE.md").write_text("# Dream Index\n", encoding="utf-8")
        (tmp_path / "decision.md").write_text("une decision consolidee\n", encoding="utf-8")

        bundle = load_context.build_bundle("un objectif", token_budget=2000)
        assert bundle["retrieval_mode"] == "plain"
        assert "Dream Index" in bundle["claude_md"]


class TestNoOrphanedProcesses:
    def test_timeout_kills_the_whole_tree(self, monkeypatch):
        """On Windows the child is cmd.exe and the CLI is its grandchild, so
        proc.kill() leaves the CLI running. Twenty such orphans held 2.9 GB and
        the machine killed the cycle instead."""
        import subprocess

        killed = {}

        class _Hanging:
            pid = 4242
            returncode = None

            def communicate(self, input=None, timeout=None):
                raise subprocess.TimeoutExpired("claude", timeout or 1)

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(llm, "claude_bin", lambda: r"C:\fake\claude.cmd")
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Hanging())
        monkeypatch.setattr(
            subprocess, "run",
            lambda argv, **k: killed.update(argv=argv) or subprocess.CompletedProcess(argv, 0),
        )

        try:
            llm._ask_claude("sys", "prompt", timeout=0.01)
        except llm.LLMError:
            pass
        else:
            raise AssertionError("a timeout must surface as LLMError")

        assert killed.get("argv") == ["taskkill", "/F", "/T", "/PID", "4242"]


class TestCatchUp:
    def _buffer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dream_buffer, "BUFFER_DIR", tmp_path)
        monkeypatch.setattr(dream_buffer, "_CONSOLIDATED", tmp_path / ".consolidated")
        return tmp_path

    def test_missed_nights_are_still_pending(self, tmp_path, monkeypatch):
        """The regression: the cycle read today's buffer only, so a night the
        machine was asleep was lost for good."""
        buf = self._buffer(tmp_path, monkeypatch)
        for day in ("2026-08-04", "2026-08-05", "2026-08-06"):
            (buf / f"{day}.jsonl").write_text('{"content": "x"}\n', encoding="utf-8")

        assert [d.isoformat() for d in dream_buffer.pending_days()] == [
            "2026-08-04", "2026-08-05", "2026-08-06",
        ]

    def test_consolidated_days_drop_off(self, tmp_path, monkeypatch):
        buf = self._buffer(tmp_path, monkeypatch)
        (buf / "2026-08-04.jsonl").write_text('{"content": "x"}\n', encoding="utf-8")
        (buf / "2026-08-05.jsonl").write_text('{"content": "y"}\n', encoding="utf-8")

        dream_buffer.mark_consolidated(dt.date(2026, 8, 4))
        assert [d.isoformat() for d in dream_buffer.pending_days()] == ["2026-08-05"]

    def test_new_content_reopens_a_consolidated_day(self, tmp_path, monkeypatch):
        """Marking is not final: a day the cycle already handled comes back as
        soon as something is appended to it, so late events are never dropped."""
        buf = self._buffer(tmp_path, monkeypatch)
        today = dt.date.today()
        (buf / f"{today.isoformat()}.jsonl").write_text('{"content": "x"}\n', encoding="utf-8")
        dream_buffer.mark_consolidated(today)
        assert dream_buffer.pending_days() == []

        dream_buffer._unmark(today)
        assert dream_buffer.pending_days() == [today]

    def test_backlog_is_capped(self, tmp_path, monkeypatch):
        """A machine left off for a month must not turn one night into an
        unbounded run."""
        buf = self._buffer(tmp_path, monkeypatch)
        for i in range(1, 29):
            (buf / f"2026-06-{i:02d}.jsonl").write_text('{"content": "x"}\n', encoding="utf-8")
        pending = dream_buffer.pending_days()
        assert len(pending) == 14
        assert pending[-1].isoformat() == "2026-06-28", "the cap keeps the most recent days"


class TestLocalModelDegradesOnce:
    def test_classifier_asks_once_then_stops(self, monkeypatch):
        """A cold 9.6 GB model cannot load inside one 20 s call, so retrying it
        per event pays the timeout every time and never succeeds. The first
        failure must settle it for the whole run."""
        import load_bearing

        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            raise RuntimeError("ollama cold")

        monkeypatch.setattr(load_bearing.llm, "ask", _boom)
        monkeypatch.setattr(load_bearing, "_llm_down", False)
        monkeypatch.setattr(load_bearing, "_load_cache", dict)
        monkeypatch.setattr(load_bearing, "_save_cache", lambda c: None)

        text = "Decision retenue: on garde la convention de nommage actuelle."
        for _ in range(50):
            load_bearing.classify(text)
        assert calls["n"] == 1, f"asked {calls['n']} times, should have asked once"

    def test_sanitisation_still_redacts_when_the_model_is_down(self, monkeypatch):
        """Degrading must not weaken redaction: the regex pass is the strict
        path, the LLM pass is only a second opinion on top of it."""
        import sanitize_local

        monkeypatch.setattr(sanitize_local, "_llm_down", True)
        out = sanitize_local.sanitize("token ghp_" + "a" * 40 + " et IBAN FR7630006000011234567890189")
        assert "ghp_" not in out.text and "<SECRET:github_pat>" in out.text
        assert "<IBAN>" in out.text


class TestDebateBudget:
    def test_biggest_clusters_win_the_budget(self, monkeypatch):
        """The ranking is the whole point of the cap: a point made in six
        places must be debated before a one-off line."""
        import scheduler

        events = [{"type": "fact", "content": f"regle numero {i} sur la convention retenue"} for i in range(40)]
        clusters = scheduler._cluster_events(events)
        ranked = sorted(clusters, key=lambda c: len(c["events"]), reverse=True)
        sizes = [len(c["events"]) for c in ranked]
        assert sizes == sorted(sizes, reverse=True)
        assert sum(sizes) == len(events), "ranking must not lose or duplicate events"


class TestSessionScan:
    def test_module_self_check(self):
        """Extraction, byte-offset resume and dedupe on a synthetic transcript."""
        session_scan.demo()

    def test_subagent_and_audit_logs_are_not_memory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DREAM_TRANSCRIPT_ROOTS", str(tmp_path))
        (tmp_path / "subagents").mkdir()
        (tmp_path / "subagents" / "agent-1.jsonl").write_text("{}\n", encoding="utf-8")
        (tmp_path / "audit.jsonl").write_text("{}\n", encoding="utf-8")
        (tmp_path / "session.jsonl").write_text("{}\n", encoding="utf-8")

        names = [p.name for p in session_scan.transcripts()]
        assert names == ["session.jsonl"]

    def test_harness_injections_are_not_the_user_talking(self):
        """A first scan is otherwise mostly task-notification and
        system-reminder blocks: they sit in the user turn but nobody said them."""
        for noise in (
            "<task-notification> <task-id>abc</task-id> decision urgente",
            "<system-reminder>Toujours utiliser bash pour ecrire</system-reminder>",
            "<command-message>The skill is loading</command-message> regle",
        ):
            entry = {"type": "user", "message": {"content": noise}}
            assert session_scan.extract_text(entry) == "", noise[:40]

    def test_tool_blocks_are_ignored_prose_is_kept(self):
        entry = {
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                {"type": "text", "text": "Decision: on garde le format ASCF."},
            ]},
        }
        assert session_scan.extract_text(entry) == "Decision: on garde le format ASCF."
