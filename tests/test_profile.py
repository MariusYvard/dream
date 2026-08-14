"""Tests for the model/embedding profile (lite mode + env overrides)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "dream" / "scripts"))

import model_profile


class TestProfile:
    def test_full_is_the_default(self, monkeypatch):
        for v in ("DREAM_PROFILE", "DREAM_CONSOLIDATION_MODEL", "DREAM_EMBED_MODEL", "DREAM_EMBED_DIM"):
            monkeypatch.delenv(v, raising=False)
        assert model_profile.name() == "full"
        assert model_profile.consolidation_model() == "gemma4:12b"
        assert model_profile.sanitise_model() == "gemma4:e4b"
        assert model_profile.embed_model() == "BAAI/bge-m3"
        assert model_profile.embed_dim() == 1024

    def test_lite_shrinks_the_footprint(self, monkeypatch):
        monkeypatch.setenv("DREAM_PROFILE", "lite")
        for v in ("DREAM_CONSOLIDATION_MODEL", "DREAM_EMBED_MODEL", "DREAM_EMBED_DIM", "DREAM_SANITISE_MODEL"):
            monkeypatch.delenv(v, raising=False)
        assert model_profile.name() == "lite"
        assert model_profile.consolidation_model() == "gemma4:e4b"
        assert model_profile.sanitise_model() == "gemma4:e4b"
        assert model_profile.embed_model() == "BAAI/bge-small-en-v1.5"
        assert model_profile.embed_dim() == 384

    def test_unknown_profile_falls_back_to_full(self, monkeypatch):
        monkeypatch.setenv("DREAM_PROFILE", "banana")
        monkeypatch.delenv("DREAM_CONSOLIDATION_MODEL", raising=False)
        assert model_profile.name() == "full"
        assert model_profile.consolidation_model() == "gemma4:12b"

    def test_env_override_beats_profile(self, monkeypatch):
        monkeypatch.setenv("DREAM_PROFILE", "lite")
        monkeypatch.setenv("DREAM_CONSOLIDATION_MODEL", "custom:7b")
        monkeypatch.setenv("DREAM_EMBED_DIM", "512")
        assert model_profile.consolidation_model() == "custom:7b"
        assert model_profile.embed_dim() == 512
