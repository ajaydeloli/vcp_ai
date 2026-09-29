"""Injectable wall clock (AGENTS.md rule 1).

Ingestion and audit code needs a real "now" for bitemporal ``known_from`` and run
timestamps, but must never read the wall clock inline. Classes take a ``Clock`` and call
it, so tests pass a fixed clock and this module holds the only production clock read.
Research code never uses this; it takes an explicit ``as_of_date``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime (default production clock)."""
    return datetime.now(UTC)
