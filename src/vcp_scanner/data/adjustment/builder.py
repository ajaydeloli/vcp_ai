"""Adjusted price builder: raw prices + stored adjustment factors -> daily_prices_adjusted.

This is the missing step in the raw -> adjusted -> features chain. Every Phase 4-5 engine
reads ``daily_prices_adjusted``; before this module nothing wrote to it.

Rules obeyed (AGENTS.md):
- Raw prices are never modified (rule 2). Adjusted rows are derived from the stored raw
  bars and the stored ``corporate_action_adjustments`` only, into their own table.
- No clock reads: ``computed_at`` is injected by the caller (rule 1).
- Repositories only; no direct DuckDB access (rule 3).

Versioning: each build is written under an ``adjustment_version`` that is a digest of the
cumulative factors (see ``AdjustmentEngine.adjustment_version``). New corporate actions
therefore create a new version instead of overwriting earlier history, and the
``daily_prices_adjusted_current`` view exposes exactly one version per instrument to
downstream readers. Each build rewrites an instrument's complete history, so a version is
always internally consistent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from vcp_scanner.data.adjustment.engine import AdjustmentEngine

if TYPE_CHECKING:
    from vcp_scanner.data.repositories.base import CorporateActionRepository
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )

logger = logging.getLogger(__name__)

# Bounds wide enough to mean "all history" for a daily NSE series.
_FAR_PAST = date(1900, 1, 1)
_FAR_FUTURE = date(2999, 12, 31)

STATUS_BUILT = "BUILT"
STATUS_NO_PRICES = "NO_PRICES"
STATUS_FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class AdjustedBuildResult:
    """Outcome of building adjusted prices for one instrument."""

    instrument_id: str
    status: str  # BUILT | NO_PRICES | FAILED
    adjustment_version: str | None = None
    rows_written: int = 0
    adjustments_applied: int = 0
    error: str | None = None


class AdjustedPriceBuilder:
    """Materialises adjusted daily bars from raw bars and stored adjustment factors."""

    def __init__(
        self,
        market_repository: DuckDBMarketDataRepository,
        corporate_action_repository: CorporateActionRepository,
        engine: AdjustmentEngine | None = None,
    ) -> None:
        self._market = market_repository
        self._corporate_actions = corporate_action_repository
        self._engine = engine or AdjustmentEngine()

    def build_for_instrument(
        self,
        instrument_id: str,
        *,
        computed_at: datetime,
    ) -> AdjustedBuildResult:
        """Rebuild the full adjusted history of one instrument.

        An instrument with no corporate actions still gets adjusted rows (factor 1.0),
        because downstream engines read only ``daily_prices_adjusted``.
        Rebuilding with unchanged inputs rewrites the same version and is idempotent.
        """
        candles = self._market.load_daily(instrument_id, _FAR_PAST, _FAR_FUTURE)
        if not candles:
            return AdjustedBuildResult(instrument_id=instrument_id, status=STATUS_NO_PRICES)

        adjustments = self._corporate_actions.load_adjustments(instrument_id)
        rows = self._engine.build_adjusted_rows(candles, adjustments, computed_at=computed_at)
        written = self._market.save_adjusted_daily(rows)

        version = rows[0].adjustment_version
        logger.info(
            "Adjusted %s: %d rows under %s (%d adjustment factor(s))",
            instrument_id,
            written,
            version,
            len(adjustments),
        )
        return AdjustedBuildResult(
            instrument_id=instrument_id,
            status=STATUS_BUILT,
            adjustment_version=version,
            rows_written=written,
            adjustments_applied=len(adjustments),
        )

    def build_all(
        self,
        *,
        computed_at: datetime,
        instrument_ids: list[str] | None = None,
    ) -> list[AdjustedBuildResult]:
        """Build every instrument that has raw prices (or just ``instrument_ids``).

        One instrument failing does not stop the rest; its result carries the error.
        """
        targets = (
            instrument_ids
            if instrument_ids is not None
            else self._market.load_priced_instrument_ids()
        )
        results: list[AdjustedBuildResult] = []
        for instrument_id in targets:
            try:
                results.append(
                    self.build_for_instrument(instrument_id, computed_at=computed_at)
                )
            except Exception as exc:  # noqa: BLE001 - isolate per-instrument failures
                logger.exception("Adjusted price build failed for %s", instrument_id)
                results.append(
                    AdjustedBuildResult(
                        instrument_id=instrument_id, status=STATUS_FAILED, error=str(exc)
                    )
                )
        return results
