"""Unit tests for the Ed25519 ledger.

These tests use a temporary DREAM_HOME so they never touch the real key store.
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Patch DREAM_HOME before importing ledger_sign
_TMP = tempfile.mkdtemp(prefix="dream_test_")
os.environ["DREAM_HOME"] = _TMP

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import ledger_sign  # noqa: E402 — must come after env patch

# Re-point module-level constants to the tmp dir
ledger_sign.DREAM_HOME = Path(_TMP)
ledger_sign.KEYS_DIR = Path(_TMP) / "keys"
ledger_sign.DB_PATH = Path(_TMP) / "pgt.sqlite"


def _bootstrap_db():
    """Create the minimal ledger schema in the temp DB."""
    schema = Path(__file__).parent.parent / "scripts" / "graph_schema.sql"
    with sqlite3.connect(ledger_sign.DB_PATH) as conn:
        conn.executescript(schema.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fresh_keys_and_db(tmp_path, monkeypatch):
    """Each test gets its own keys directory and blank database."""
    keys = tmp_path / "keys"
    db = tmp_path / "pgt.sqlite"
    monkeypatch.setattr(ledger_sign, "DREAM_HOME", tmp_path)
    monkeypatch.setattr(ledger_sign, "KEYS_DIR", keys)
    monkeypatch.setattr(ledger_sign, "DB_PATH", db)
    # Bootstrap schema
    schema = Path(__file__).parent.parent / "scripts" / "graph_schema.sql"
    with sqlite3.connect(db) as conn:
        conn.executescript(schema.read_text(encoding="utf-8"))
    # Generate keys
    ledger_sign.bootstrap()
    yield


class TestBootstrap:
    def test_key_files_created(self, tmp_path):
        keys = tmp_path / "keys"
        assert (keys / "ed25519.private").exists()
        assert (keys / "ed25519.public").exists()

    def test_private_key_permissions(self, tmp_path):
        import stat
        priv = tmp_path / "keys" / "ed25519.private"
        mode = oct(stat.S_IMODE(priv.stat().st_mode))
        # On Unix: 0o600. Windows does not enforce Unix permissions; skip.
        if os.name != "nt":
            assert mode == "0o600", f"expected 0o600, got {mode}"

    def test_bootstrap_is_idempotent(self, tmp_path):
        """Second bootstrap call must not overwrite existing key."""
        priv = tmp_path / "keys" / "ed25519.private"
        original = priv.read_bytes()
        ledger_sign.bootstrap()
        assert priv.read_bytes() == original


class TestAppendAndVerify:
    def test_empty_ledger_verifies(self):
        assert ledger_sign.verify() is True

    def test_single_leaf_verifies(self):
        ledger_sign.append_leaf("test_op", "node-1", {"key": "value"})
        assert ledger_sign.verify() is True

    def test_ten_leaves_verify(self):
        for i in range(10):
            ledger_sign.append_leaf("op", f"node-{i}", {"i": i})
        assert ledger_sign.verify() is True

    def test_tampered_leaf_fails_verify(self, tmp_path):
        ledger_sign.append_leaf("op", "node-x", {"data": "original"})
        db = tmp_path / "pgt.sqlite"
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE ledger SET payload_sha256 = 'deadbeef' WHERE leaf_id = 1")
            conn.commit()
        assert ledger_sign.verify() is False

    def test_leaf_return_shape(self):
        result = ledger_sign.append_leaf("store_event", "node-abc", {"x": 1})
        assert "leaf_id" in result
        assert "payload_sha" in result
        assert "merkle_root" in result
        assert "public_key_fp" in result
        # payload_sha must be a valid hex sha256
        assert len(result["payload_sha"]) == 64
