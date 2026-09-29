"""Corporate Action Ingestion Worker."""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, date, datetime

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.identity import InstrumentResolver, canonical_instrument_id
from vcp_scanner.data.providers.base import CorporateActionProvider
from vcp_scanner.data.reconciliation.engine import ReconciliationEngine
from vcp_scanner.data.repositories.base import CorporateActionRepository
from vcp_scanner.domain.market import Instrument

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
    ) -> None:
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

        ``known_at`` is the ingestion timestamp (default: now). It is also the reference
        for the secondary-source grace period: an action first seen today is judged as of
        today, not as of the end of the requested window, so a backfill of old actions does
        not sit at SINGLE_SOURCE forever.
        """
        known_at = known_at or datetime.now(UTC)
        as_of = known_at.date()

        logger.info(f"Fetching primary (NSE) corporate actions from {start} to {end}")
        primary_actions = self.primary_provider.get_actions(start, end, instruments)

        logger.info(f"Fetching secondary (Upstox) corporate actions from {start} to {end}")
        secondary_actions = self.secondary_provider.get_actions(start, end, instruments)

        all_new_actions = primary_actions + secondary_actions

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

        logger.info(
            "Fetched %d raw corporate actions, %d new.", len(saved_actions), new_rows
        )

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

        logger.info(
            "Reconciliation complete: %d resolutions, %d adjustment factors.",
            reconciled_count,
            adjusted_count,
        )
