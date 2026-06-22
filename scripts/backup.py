"""Weekly backup of the irreplaceable PGT state.

The vector store rebuilds and the day buffer is transient, but the SQLite
graph, the signed ledger inside it and the Ed25519 identity keys cannot be
regenerated (the graph was already wiped once). This snapshots the load-bearing
state into DREAM_HOME/backups/<stamp>/ and keeps the most recent N. The SQLite
file is copied through the online backup API so the snapshot is consistent even
while WAL is active.

    python backup.py            # take a backup, keep the last 8
    python backup.py --keep 4
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys
from pathlib import Path

DREAM_HOME = Path(os.environ.get("DREAM_HOME", Path.home() / ".dream"))
BACKUP_ROOT = DREAM_HOME / "backups"

# Copied verbatim (relative to DREAM_HOME). The SQLite DB is handled separately.
ITEMS = ["graph.gpickle", "circuit.json", "keys", "topics"]


def _backup_sqlite(dest_dir: Path) -> bool:
    db = DREAM_HOME / "pgt.sqlite"
    if not db.exists():
        return False
    with sqlite3.connect(db) as src, sqlite3.connect(dest_dir / "pgt.sqlite") as dst:
        src.backup(dst)
    return True


def take_backup(keep: int = 8) -> Path:
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_ROOT / stamp
    dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    if _backup_sqlite(dest):
        copied += 1
    for rel in ITEMS:
        src = DREAM_HOME / rel
        if not src.exists():
            continue
        target = dest / rel
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        copied += 1

    _prune(keep)
    print(f"[backup] {copied} items -> {dest}")
    return dest


def _prune(keep: int) -> None:
    if not BACKUP_ROOT.exists() or keep <= 0:
        return
    snapshots = sorted((p for p in BACKUP_ROOT.iterdir() if p.is_dir()), key=lambda p: p.name)
    for old in snapshots[:-keep]:
        shutil.rmtree(old, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the irreplaceable PGT state.")
    parser.add_argument("--keep", type=int, default=8, help="snapshots to retain (default 8)")
    args = parser.parse_args()
    try:
        take_backup(args.keep)
        return 0
    except Exception as exc:
        print(f"[backup] FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
