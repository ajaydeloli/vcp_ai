"""Corporate Action Ingestion Worker."""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime
from typing import Protocol

from vcp_scanner.data.adjustment.engine import AdjustmentEngine, ex_date_prices
from vcp_scanner.data.identity import InstrumentResolver, canonical_instrument_id
from vcp_scanner.data.providers.base import CorporateActionProvider
from vcp_scanner.data.quality.events import corporate_action_events
from vcp_scanner.data.reconciliation.engine import ReconciliationEngine
from vcp_scanner.data.repositories.base import CorporateActionRepository
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.domain.corporate_actions import (
    PRICE_DERIVED_ACTIONS,
    CorporateActionAdjustment,
    CorporateActionResolution,
    ExDatePrices,
)
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.errors import ProviderAuthError
from vcp_scanner.domain.market import Candle, Instrument
from vcp_scanner.infrastructure.clock import Clock, utc_now

logger = logging.getLogger(__name__)


class CorporateActionIngestionWorker:
    """Orchestrates fetching, saving, reconciling, and adjusting corporate actions."""

    def __init__(
        self,
        primary_provider: CorporateActionProvider,
        secondary_provider: CorporateActionProvider,
        repository: CorporateActionRepository,
        reconciliation_engine: ReconciliationEngine,
        adjustment_engine: AdjustmentEngine,
        resolver: InstrumentResolver | None = None,
        *,
        clock: Clock = utc_now,
        quality_repository: DuckDBDataQualityRepository | None = None,
        conflict_blocks_signals: bool = True,
        market: DailyBarSource | None = None,
        manual_provider: CorporateActionProvider | None = None,
    ) -> None:
        self._clock = clock
        # Optional (audit 2.7d): hand-entered actions (source MANUAL). Reconciliation gives
        # their (type, ex-date) MANUAL_OVERRIDE.
        self._manual = manual_provider
        #: Number of manual actions served by the last ``run``.
        self.manual_count = 0
        # Optional (audit step 2.4): raw bars, from which rights and demerger factors are
        # derived. Without it those actions get no factor (the pre-2.4 behavior).
        self._market = market
        #: Set by ``run`` when the secondary source rejected its credentials (NSE-only run).
        self.secondary_unavailable: str | None = None
        # Optional (audit P0-2): keep CORPORATE_ACTION_UNRESOLVED events in step with the
        # current PROVIDER_CONFLICT resolutions so the signal gate can see them.
        self._quality = quality_repository
        self._conflict_blocks_signals = conflict_blocks_signals
        self.primary_provider = primary_provider
        self.secondary_provider = secondary_provider
        self.repository = repository
        self.reconciliation_engine = reconciliation_engine
        self.adjustment_engine = adjustment_engine
        self.resolver = resolver

    def run(
        self,
        start: date,
        end: date,
        instruments: list[Instrument] | None = None,
        *,
        known_at: datetime | None = None,
    ) -> None:
        """Run the end-to-end ingestion and reconciliation for a date range.

        ``known_at`` is the ingestion timestamp (default: the injected clock). It is also the
        reference for the secondary-source grace period: an action first seen today is judged as of
        today, not as of the end of the requested window, so a backfill of old actions does
        not sit at SINGLE_SOURCE forever.
        """
        known_at = known_at or self._clock()
        as_of = known_at.date()

        logger.info(f"Fetching primary (NSE) corporate actions from {start} to {end}")
        primary_actions = self.primary_provider.get_actions(start, end, instruments)

        logger.info(f"Fetching secondary (Upstox) corporate actions from {start} to {end}")
        self.secondary_unavailable = None
        try:
            secondary_actions = self.secondary_provider.get_actions(start, end, instruments)
        except ProviderAuthError as exc:
            # Owner decision (2026-10-01): rejected credentials (e.g. an expired Upstox token)
            # downgrade the run to NSE-only instead of aborting it. Nothing from the secondary
            # counts as evidence, so no action is escalated because Upstox was silent.
            self.secondary_unavailable = str(exc)
            logger.warning("Secondary corporate-action source unavailable, NSE only: %s", exc)
            secondary_actions = []

        manual_actions = (
            self._manual.get_actions(start, end, instruments) if self._manual is not None else []
        )
        self.manual_count = len(manual_actions)

        all_new_actions = primary_actions + secondary_actions + manual_actions

        # Which instruments the secondary source was actually asked about (audit P0-2). A
        # provider that does not report coverage (e.g. no Upstox token) covers nothing, so its
        # silence never escalates an NSE-only split/bonus to PROVIDER_CONFLICT.
        # Coverage is also bounded in time: a provider may only serve recent events (Upstox
        # returns about 12 months), so its silence counts only from the earliest date it
        # actually returned for that instrument (``coverage_start``; found in audit Fix 5b).
        # A provider that reports queried ids but no coverage dates is trusted for the whole
        # requested window.
        queried = (
            set()
            if self.secondary_unavailable
            else getattr(self.secondary_provider, "queried_instrument_ids", None) or set()
        )
        coverage = getattr(self.secondary_provider, "coverage_start", None)
        secondary_windows: dict[str, tuple[date, date]] = {}
        for provider_iid in queried:
            if coverage is None:
                window_start = start
            elif provider_iid in coverage:
                window_start = max(start, coverage[provider_iid])
            else:
                continue  # asked, but no records at all: its silence proves nothing
            if window_start <= end:
                secondary_windows[canonical_instrument_id(self.resolver, provider_iid)] = (
                    window_start,
                    end,
                )

        if not all_new_actions:
            logger.info("No corporate actions found in this period.")
            return

        # 1. Save raw actions, each under the instrument's permanent ID.
        # NSE reports only symbol + ISIN and Upstox is queried per instrument, so the
        # providers' IDs are provisional. Remapping here (ISIN first) makes both sources
        # land on the same instrument_id, which reconciliation and adjustment depend on.
        saved_actions = []
        new_rows = 0
        for action in all_new_actions:
            canonical_id = canonical_instrument_id(
                self.resolver, action.instrument_id, isin=action.isin
            )
            # ingested_at records when we first saw it, for grace period logic.
            action_with_ingestion = dataclasses.replace(
                action, instrument_id=canonical_id, ingested_at=known_at
            )
            # Idempotent: an observation we already hold keeps its original first-seen time.
            if self.repository.save_corporate_action(action_with_ingestion, known_from=known_at):
                new_rows += 1
            saved_actions.append(action_with_ingestion)

        logger.info("Fetched %d raw corporate actions, %d new.", len(saved_actions), new_rows)

        # Group by instrument to run reconciliation per instrument
        instrument_ids = {a.instrument_id for a in saved_actions}

        reconciled_count = 0
        adjusted_count = 0

        # 2. Reconcile and calculate factors per instrument
        for iid in instrument_ids:
            # Load ALL existing raw actions for this instrument (past and present)
            # This is necessary because reconciliation compares all actions.
            all_historical_actions = self.repository.load_corporate_actions(iid)

            # Load existing resolutions to detect updates vs new
            existing_resolutions = self.repository.load_resolutions(iid)

            # Reconcile
            results = self.reconciliation_engine.reconcile(
                instrument_id=iid,
                actions=all_historical_actions,
                as_of_date=as_of,
                existing_resolutions=existing_resolutions,
                secondary_window=secondary_windows.get(iid),
            )

            # Any changed resolution, new OR updated, is persisted. Updates matter: a
            # SINGLE_SOURCE that becomes CONFIRMED, or falls to PROVIDER_CONFLICT after the
            # grace period, changes what may feed adjustment. save_resolution closes the
            # superseded row, so history stays queryable.
            changed = False
            for result in results:
                self.repository.save_resolution(result.resolution, known_from=known_at)
                reconciled_count += 1
                changed = True

            # 3. Rebuild the instrument's factor set from the current resolutions whenever a
            # resolution changed OR the stored factors differ from what the engine computes
            # now (e.g. after an engine fix: same-day split+bonus used to lose a factor,
            # found in audit Fix 5b). compute_factors drops non-adjustable statuses, and
            # replace_adjustments retires factors that no longer apply.
            current = self.repository.load_resolutions(iid)
            prices = self._ex_prices(iid, current)
            new_adjustments = self.adjustment_engine.compute_factors(current, prices)
            if changed or _factor_key(new_adjustments) != _factor_key(
                self.repository.load_adjustments(iid)
            ):
                self.repository.replace_adjustments(iid, new_adjustments, known_from=known_at)
                adjusted_count += len(new_adjustments)

            # 4. Publish unresolved conflicts to the signal gate. Done for every reconciled
            # instrument, changed or not, so a conflict that was superseded is closed and a
            # standing one stays open.
            if self._quality is not None:
                self._quality.sync_events(
                    iid,
                    DataQualityFlag.CORPORATE_ACTION_UNRESOLVED,
                    corporate_action_events(
                        iid,
                        self.repository.load_resolutions(iid),
                        known_at,
                        conflict_blocks_signals=self._conflict_blocks_signals,
                        ex_prices=prices,
                    ),
                    at=known_at,
                )

        logger.info(
            "Reconciliation complete: %d resolutions, %d adjustment factors.",
            reconciled_count,
            adjusted_count,
        )

    def _ex_prices(
        self, instrument_id: str, resolutions: list[CorporateActionResolution]
    ) -> dict[date, ExDatePrices]:
        """Raw prices around the ex-dates of this instrument's rights issues and demergers."""
        ex_dates = [
            r.ex_date
            for r in resolutions
            if r.action_type in PRICE_DERIVED_ACTIONS and r.ex_date is not None
        ]
        if self._market is None or not ex_dates:
            return {}
        candles = self._market.load_daily(instrument_id, date(1900, 1, 1), date(2999, 12, 31))
        return ex_date_prices(candles, ex_dates)


class DailyBarSource(Protocol):
    def load_daily(self, instrument_id: str, start: date, end: date) -> list[Candle]: ...


def _factor_key(adjustments: list[CorporateActionAdjustment]) -> list[tuple[str, float, float]]:
    """What matters about a factor set for prices: dates and factors, rounded."""
    return sorted(
        (a.effective_date.isoformat(), round(float(a.price_factor), 10),
         round(float(a.volume_factor), 10))
        for a in adjustments
    )  # fmt: skip
