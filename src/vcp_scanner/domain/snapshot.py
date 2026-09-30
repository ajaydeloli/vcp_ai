"""Data snapshot identity (DATABASE_SCHEMA data_snapshots; audit finding P0-1).

A data snapshot freezes *what was known* at a system time. Everything derived from
prices (adjusted bars, features, weekly bars, RS, Trend results) is tagged with the
``data_snapshot_id`` it was computed from, so a later price correction or corporate
action can never silently change an earlier result.

``LIVE_SNAPSHOT_ID`` is the explicit "not frozen" marker. Rows under it are working
data for day-to-day use and must not be used to validate thresholds or backtests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

LIVE_SNAPSHOT_ID = "LIVE"

_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def validate_snapshot_id(snapshot_id: str) -> str:
    """Return ``snapshot_id`` if it is a safe identifier, else raise ``ValueError``."""
    if not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise ValueError(f"Invalid data snapshot id: {snapshot_id!r}")
    return snapshot_id


def snapshot_id_for(known_at: datetime) -> str:
    """Deterministic id for a ``known_at`` instant (UTC, second resolution).

    The same ``known_at`` always yields the same id, so creating a snapshot twice for
    one instant is idempotent.
    """
    if known_at.tzinfo is None:
        raise ValueError("known_at must be timezone-aware")
    return "snap-" + known_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True, slots=True)
class DataSnapshot:
    """A frozen view of the data: everything with ``known_from <= known_at``."""

    data_snapshot_id: str
    known_at: datetime
    created_at: datetime
    description: str | None = None
