"""Repository protocols and interfaces (PROJECT_DESIGN section 48).

Strategy code interacts only with repository interfaces and never touches
DuckDB or Parquet files directly (AGENTS.md hard rule 3).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from vcp_scanner.domain.corporate_actions import (
    CorporateAction,
    CorporateActionAdjustment,
    CorporateActionResolution,
)
from vcp_scanner.domain.features import AdjustedClose, DailyFeatures, WeeklyPrice
from vcp_scanner.domain.fundamentals import FundamentalSnapshot
from vcp_scanner.domain.market import Candle, Instrument
from vcp_scanner.domain.trend import (
    RelativeStrengthResult,
    TrendConditionResult,
    TrendTemplateResult,
    WeeklyContext,
)
from vcp_scanner.domain.universe import UniverseMembership, UniverseSnapshot


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

    def earliest_timestamp(self, instrument_id: str) -> datetime | None:
        """Return the earliest timestamp recorded for this instrument, or None."""
        ...


@runtime_checkable
class InstrumentRepository(Protocol):
    """Persistence abstraction for the security master."""

    def save_instruments(self, instruments: list[Instrument]) -> int:
        """Upsert instruments. Returns number of records modified."""
        ...

    def load_instruments(self) -> list[Instrument]:
        """Load all currently active instruments."""
        ...


@runtime_checkable
class UniverseRepository(Protocol):
    """Persistence abstraction for universe snapshots and memberships."""

    def save_snapshot(
        self,
        snapshot: UniverseSnapshot,
        memberships: list[UniverseMembership],
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


@runtime_checkable
class CorporateActionRepository(Protocol):
    """Persistence abstraction for corporate actions and resolution.

    All tables are bitemporal (known_from/known_to). Point-in-time reads
    use known_at to reconstruct the world as it was seen at a past time.
    See DATABASE_SCHEMA §16, §17, §17A.
    """

    # -- Raw corporate actions (one row per source observation) --

    def save_corporate_action(
        self,
        action: CorporateAction,
        known_from: datetime,
    ) -> bool:
        """Append a raw corporate action observation from a single provider.

        Idempotent: returns False (and writes nothing) if it is already held as current.
        """
        ...

    def load_corporate_actions(
        self,
        instrument_id: str,
        *,
        known_at: datetime | None = None,
    ) -> list[CorporateAction]:
        """Load current (or as-of known_at) raw actions for an instrument."""
        ...

    # -- Resolution (cross-source reconciled records) --

    def save_resolution(
        self,
        resolution: CorporateActionResolution,
        known_from: datetime,
    ) -> None:
        """Append a resolution row, closing any prior current row for the
        same (instrument_id, action_type, ex_date)."""
        ...

    def load_resolutions(
        self,
        instrument_id: str,
        *,
        known_at: datetime | None = None,
    ) -> list[CorporateActionResolution]:
        """Load current (or as-of known_at) resolutions for an instrument."""
        ...

    # -- Adjustment factors (derived from resolutions) --

    def save_adjustment(
        self,
        adjustment: CorporateActionAdjustment,
        known_from: datetime,
    ) -> None:
        """Append an adjustment factor row."""
        ...

    def replace_adjustments(
        self,
        instrument_id: str,
        adjustments: list[CorporateActionAdjustment],
        known_from: datetime,
    ) -> None:
        """Make ``adjustments`` the full current factor set, retiring all other open rows."""
        ...

    def load_adjustments(
        self,
        instrument_id: str,
        *,
        known_at: datetime | None = None,
    ) -> list[CorporateActionAdjustment]:
        """Load current (or as-of known_at) adjustments for an instrument,
        ordered by effective_date ascending."""
        ...


@runtime_checkable
class FeatureRepository(Protocol):
    """Persistence abstraction for derived technical features."""

    def save_daily_features(self, features: list[DailyFeatures]) -> None:
        """Save computed daily features."""
        ...

    def save_weekly_prices(self, prices: list[WeeklyPrice]) -> None:
        """Save computed weekly aggregated prices."""
        ...

    def load_daily_features(
        self,
        instrument_id: str,
        as_of: date,
        calculation_version: str | None = None,
    ) -> DailyFeatures | None:
        """Load the daily feature vector for an instrument on a specific date."""
        ...

    def load_weekly_prices(self, instrument_id: str, start: date, end: date) -> list[WeeklyPrice]:
        """Load weekly prices between start and end (inclusive)."""
        ...

    def load_daily_feature_history(
        self,
        instrument_id: str,
        as_of: date,
        limit: int,
        calculation_version: str | None = None,
    ) -> list[DailyFeatures]:
        """Load up to ``limit`` feature rows with trade_date <= as_of, newest first."""
        ...

    def load_adjusted_closes(
        self,
        instrument_id: str,
        as_of: date,
        limit: int,
    ) -> list[AdjustedClose]:
        """Load up to ``limit`` adjusted closes with trade_date <= as_of, newest first."""
        ...


@runtime_checkable
class TrendRepository(Protocol):
    """Persistence abstraction for Trend Template and weekly-context results.

    Every condition row keeps measurement, threshold and verdict
    (DATABASE_SCHEMA section 30, AGENTS.md hard rule 6).
    """

    def save_trend_template_results(
        self,
        scan_id: str,
        config_hash: str,
        results: list[TrendTemplateResult],
    ) -> None:
        """Upsert summary rows and all condition rows, atomically."""
        ...

    def load_trend_conditions(
        self,
        instrument_id: str,
        as_of_date: date,
        calculation_version: str,
        config_hash: str,
    ) -> list[TrendConditionResult]:
        """Load stored condition rows for one config, ordered by condition_id."""
        ...

    def save_weekly_context(self, contexts: list[WeeklyContext]) -> None:
        """Upsert weekly Stage classifications."""
        ...

    def load_weekly_context(
        self,
        instrument_id: str,
        as_of_date: date,
        algorithm_version: str,
    ) -> WeeklyContext | None:
        """Load the stored weekly classification for an exact as-of date."""
        ...

    def load_relative_strength(
        self,
        instrument_id: str,
        as_of_date: date,
        calculation_version: str,
    ) -> RelativeStrengthResult | None:
        """Load the RS snapshot for an exact as-of date, or None if not computed."""
        ...
