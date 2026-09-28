"""Repository protocols and interfaces (PROJECT_DESIGN section 48).

Strategy code interacts only with repository interfaces and never touches
DuckDB or Parquet files directly (AGENTS.md hard rule 3).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from vcp_scanner.domain.fundamentals import FundamentalSnapshot
from vcp_scanner.domain.market import Candle


@runtime_checkable
class MarketDataRepository(Protocol):
    """Persistence abstraction for daily and intraday candles."""

    def load_daily(
        self,
        instrument_id: str,
        start: date,
        end: date,
    ) -> list[Candle]:
        """Load raw or canonical daily bars between start and end inclusive."""
        ...

    def save_daily(self, candles: list[Candle]) -> int:
        """Append-only save of daily candles. Returns count of rows inserted."""
        ...

    def latest_timestamp(self, instrument_id: str) -> datetime | None:
        """Return the latest timestamp recorded for this instrument, or None."""
        ...


@runtime_checkable
class UniverseRepository(Protocol):
    """Persistence abstraction for universe snapshots and memberships."""

    def save_snapshot(
        self,
        snapshot_id: str,
        as_of_date: date,
        instrument_ids: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a universe snapshot for a specific date."""
        ...

    def load_snapshot(self, as_of_date: date) -> list[str]:
        """Load instrument IDs eligible in the universe as-of a given date."""
        ...


@runtime_checkable
class ScanRepository(Protocol):
    """Persistence abstraction for scan runs and qualified results."""

    def save_scan(self, scan_run: dict[str, Any]) -> None:
        """Save scan metadata, configuration hash, version tags and timestamps."""
        ...

    def save_results(self, scan_id: str, results: list[dict[str, Any]]) -> None:
        """Save qualified patterns, condition results, and score components."""
        ...

    def load_scan(self, scan_id: str) -> dict[str, Any] | None:
        """Load scan metadata and status by scan_id."""
        ...


@runtime_checkable
class FundamentalRepository(Protocol):
    """Persistence abstraction for point-in-time fundamentals."""

    def save_snapshot(self, snapshot: FundamentalSnapshot) -> None:
        """Save a point-in-time fundamental snapshot."""
        ...

    def load_snapshot(self, instrument_id: str, as_of: date) -> FundamentalSnapshot | None:
        """Load most recent fundamental snapshot available at or before as_of date."""
        ...
