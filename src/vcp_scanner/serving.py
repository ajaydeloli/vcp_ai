"""The serving copy of the database for the read-only dashboard API (FRONTEND_SPECIFICATION 67.2).

DuckDB does not let another process read while the daily run writes, so the daily run's last step
copies the checkpointed database to ``<db folder>/serving/vcp_serving.duckdb`` and the API opens
only that copy, read-only. The copy is written under a ``.partial`` name, opened and checked, and
renamed over the old copy only then, so the API never sees a half-written file and a failed
refresh leaves the previous copy in place. The copy is a cache: never backed up, never committed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from vcp_scanner.backup import check_database

SERVING_NAME = "vcp_serving.duckdb"


def default_serving_path(db: str | Path) -> Path:
    return Path(db).resolve().parent / "serving" / SERVING_NAME


def refresh_serving_copy(db: str | Path, target: str | Path | None = None) -> Path:
    """Checkpoint ``db``, copy it to ``target`` (default: ``default_serving_path``), verify the
    copy opens and answers a query, then replace the old copy atomically. Raises on any failure;
    the previous copy is then untouched."""
    src = Path(db)
    dest = Path(target) if target is not None else default_serving_path(src)
    error = check_database(src, checkpoint=True)
    if error is not None:
        raise RuntimeError(f"database failed its health check: {error}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in dest.parent.glob("*.partial"):
        stale.unlink()  # left by an interrupted earlier refresh
    size = src.stat().st_size
    free = shutil.disk_usage(dest.parent).free
    if free < size * 1.1:
        raise RuntimeError(
            f"not enough disk space for the serving copy: {free / 1e9:.1f} GB free, "
            f"{size / 1e9:.1f} GB needed in {dest.parent}"
        )
    partial = dest.with_name(dest.name + ".partial")
    shutil.copyfile(src, partial)
    with partial.open("rb") as fh:
        os.fsync(fh.fileno())
    error = check_database(partial)
    if error is not None:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"the serving copy does not open: {error}")
    partial.replace(dest)
    return dest
