"""Database health check and rolling backups before the daily run (owner decision 2026-10-02).

DuckDB itself survives a crash (a write is all or nothing), but a power failure can still
damage the file on disk, and rebuilding means downloading years of NSE data again. So
``vcp run daily`` first checks that the database opens and answers a query, then copies it to
``<db folder>/backups/`` and keeps the newest ``keep`` copies.

The copy is written under a ``.partial`` name and renamed only after it has been opened and
checked, so a power cut during the copy never leaves something that looks like a good backup.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import duckdb

BACKUP_RE = re.compile(r"^(?P<stem>.+)_(?P<ts>\d{8}_\d{6})\.duckdb$")
DEFAULT_KEEP = 3


def default_backup_dir(db: str | Path) -> Path:
    return Path(db).resolve().parent / "backups"


def check_database(db: str | Path, *, checkpoint: bool = False) -> str | None:
    """None when the database opens and answers a query; otherwise the error text.

    With ``checkpoint`` the write-ahead log is folded into the main file first, so a plain
    file copy afterwards is a complete database.
    """
    path = Path(db)
    if not path.exists():
        return f"database file not found: {path}"
    try:
        con = duckdb.connect(str(path), read_only=not checkpoint)
        try:
            if checkpoint:
                con.execute("CHECKPOINT")
            con.execute("SELECT count(*) FROM information_schema.tables").fetchone()
        finally:
            con.close()
    except Exception as exc:  # any failure to open or read means "do not trust this file"
        return f"{type(exc).__name__}: {exc}"
    return None


def list_backups(backup_dir: Path, stem: str) -> list[Path]:
    """Finished backups of the database named ``stem``, newest first."""
    if not backup_dir.is_dir():
        return []
    found = []
    for p in backup_dir.iterdir():
        m = BACKUP_RE.match(p.name)
        if m and m.group("stem") == stem:
            found.append((m.group("ts"), p))
    return [p for _, p in sorted(found, reverse=True)]


def backup_database(
    db: str | Path, backup_dir: Path, *, keep: int = DEFAULT_KEEP, now: datetime
) -> Path:
    """Checkpoint, copy, verify and rotate; returns the new backup. Raises on any failure."""
    src = Path(db)
    error = check_database(src, checkpoint=True)
    if error is not None:
        raise RuntimeError(f"database failed its health check: {error}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for stale in backup_dir.glob("*.partial"):
        stale.unlink()  # left by an interrupted earlier backup
    size = src.stat().st_size
    free = shutil.disk_usage(backup_dir).free
    if free < size * 1.1:
        raise RuntimeError(
            f"not enough disk space for a backup: {free / 1e9:.1f} GB free, "
            f"{size / 1e9:.1f} GB needed in {backup_dir}"
        )
    target = backup_dir / f"{src.stem}_{now:%Y%m%d_%H%M%S}.duckdb"
    partial = target.with_name(target.name + ".partial")
    shutil.copyfile(src, partial)
    with partial.open("rb") as fh:
        os.fsync(fh.fileno())
    error = check_database(partial)
    if error is not None:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"the backup copy does not open: {error}")
    partial.replace(target)
    for old in list_backups(backup_dir, src.stem)[keep:]:
        old.unlink()
    return target


def restore_hint(db: str | Path, backup_dir: Path) -> str:
    """What to do when the database is damaged: the newest backup and the command to restore."""
    backups = list_backups(backup_dir, Path(db).stem)
    if not backups:
        return f"No backup found in {backup_dir}."
    newest = backups[0]
    return (
        f"Newest backup: {newest}\n"
        f"To restore it (keep the damaged file for inspection):\n"
        f"  mv {db} {db}.damaged && cp {newest} {db}"
    )
