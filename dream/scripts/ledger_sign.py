"""Ed25519-signed ledger with chained sha256 leaves and a Merkle root.

Bootstrap once:
    python ledger_sign.py --bootstrap

Append a leaf programmatically:
    from ledger_sign import append_leaf
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
KEYS_DIR = DREAM_HOME / "keys"
DB_PATH = DREAM_HOME / "pgt.sqlite"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def bootstrap() -> None:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_path = KEYS_DIR / "ed25519.private"
    pub_path = KEYS_DIR / "ed25519.public"
    if priv_path.exists():
        print("[ledger] private key already exists, refusing to overwrite")
        return
    priv_path.write_bytes(
        priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        pub.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    priv_path.chmod(0o600)
    fp = hashlib.sha256(pub_path.read_bytes()).hexdigest()
    print(f"[ledger] keys written, public fingerprint = {fp}")


def _load_keys() -> tuple[Ed25519PrivateKey, str]:
    priv = serialization.load_pem_private_key(
        (KEYS_DIR / "ed25519.private").read_bytes(), password=None
    )
    pub_bytes = (KEYS_DIR / "ed25519.public").read_bytes()
    fp = hashlib.sha256(pub_bytes).hexdigest()
    return priv, fp


def _merkle_step(root: str | None, leaf: str) -> str:
    if root is None:
        return leaf
    return hashlib.sha256((root + leaf).encode()).hexdigest()


def append_leaf(operation: str, node_id: str | None, payload: dict[str, Any]) -> dict[str, str]:
    """Append a signed leaf to the ledger. Returns the new state."""
    priv, fp = _load_keys()
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    payload_sha = hashlib.sha256(raw).hexdigest()
    signature = priv.sign(raw).hex()

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT merkle_root, last_leaf FROM ledger_state WHERE id = 1")
        row = cur.fetchone()
        prev_root, prev_leaf = (row if row else (None, None))
        new_root = _merkle_step(prev_root, payload_sha)
        cur.execute(
            "INSERT INTO ledger (payload_sha256, parent_leaf, signature, public_key_fp, operation, node_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payload_sha, prev_leaf, signature, fp, operation, node_id, _now_iso()),
        )
        leaf_id = cur.lastrowid
        cur.execute(
            "INSERT OR REPLACE INTO ledger_state (id, merkle_root, last_leaf, updated_at) VALUES (1, ?, ?, ?)",
            (new_root, leaf_id, _now_iso()),
        )
        conn.commit()

    return {"leaf_id": str(leaf_id), "payload_sha": payload_sha, "merkle_root": new_root, "public_key_fp": fp}


def verify() -> bool:
    """Recompute the chain and compare with the stored Merkle root."""
    if not DB_PATH.exists():
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT payload_sha256 FROM ledger ORDER BY leaf_id ASC")
        leaves = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT merkle_root FROM ledger_state WHERE id = 1")
        row = cur.fetchone()
    stored = row[0] if row else None
    root: str | None = None
    for leaf in leaves:
        root = _merkle_step(root, leaf)
    return stored == root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.bootstrap:
        bootstrap()
    elif args.verify:
        ok = verify()
        print("OK" if ok else "FAIL")
        sys.exit(0 if ok else 2)
    else:
        parser.print_help()
