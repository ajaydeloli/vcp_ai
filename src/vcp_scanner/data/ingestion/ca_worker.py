"""Corporate Action Ingestion Worker."""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.identity import InstrumentResolver, canonical_instrument_id
from vcp_scanner.data.providers.base import CorporateActionProvider
from vcp_scanner.data.quality.events import corporate_action_events
from vcp_scanner.data.reconciliation.engine import ReconciliationEngine
from vcp_scanner.data.repositories.base import CorporateActionRepository
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.market import Instrument
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
    ) -> None:
        self._clock = clock
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
        secondary_actions = self.secondary_provider.get_actions(start, end, instruments)

        all_new_actions = primary_actions + secondary_actions

        # Which instruments the secondary source was actually asked about (audit P0-2). A
        # provider that does not report coverage (e.g. no Upstox token) covers nothing, so its
        # silence never escalates an NSE-only split/bonus to PROVIDER_CONFLICT.
        queried = getattr(self.secondary_provider, "queried_instrument_ids", None) or set()
        secondary_covered = {canonical_instrument_id(self.resolver, iid) for iid in queried}

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
                secondary_window=(start, end) if iid in secondary_covered else None,
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

            # 3. If any resolution changed, rebuild the instrument's full factor set from
            # the current resolutions. compute_factors drops non-adjustable statuses, and
            # replace_adjustments retires factors that no longer apply.
            if changed:
                current_resolutions = self.repository.load_resolutions(iid)
                new_adjustments = self.adjustment_engine.compute_factors(current_resolutions)
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
                    ),
                    at=known_at,
                )

        logger.info(
            "Reconciliation complete: %d resolutions, %d adjustment factors.",
            reconciled_count,
            adjusted_count,
        )
