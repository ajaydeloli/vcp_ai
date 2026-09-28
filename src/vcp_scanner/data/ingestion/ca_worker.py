"""Corporate Action Ingestion Worker."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
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
    ) -> None:
        self.primary_provider = primary_provider
        self.secondary_provider = secondary_provider
        self.repository = repository
        self.reconciliation_engine = reconciliation_engine
        self.adjustment_engine = adjustment_engine

    def run(self, start: date, end: date, instruments: list[Instrument] | None = None) -> None:
        """Run the end-to-end ingestion and reconciliation for a date range."""
        known_at = datetime.now(UTC)

        logger.info(f"Fetching primary (NSE) corporate actions from {start} to {end}")
        primary_actions = self.primary_provider.get_actions(start, end, instruments)

        logger.info(f"Fetching secondary (Upstox) corporate actions from {start} to {end}")
        secondary_actions = self.secondary_provider.get_actions(start, end, instruments)

        all_new_actions = primary_actions + secondary_actions

        if not all_new_actions:
            logger.info("No corporate actions found in this period.")
            return

        # 1. Save raw actions
        for action in all_new_actions:
            # We add ingested_at to track when we first saw it, for grace period logic
            # Use dataclasses.replace to keep immutability
            import dataclasses

            action_with_ingestion = dataclasses.replace(action, ingested_at=known_at)
            self.repository.save_corporate_action(action_with_ingestion, known_from=known_at)

        logger.info(f"Saved {len(all_new_actions)} raw corporate actions.")

        # Group by instrument to run reconciliation per instrument
        instrument_ids = {a.instrument_id for a in all_new_actions}

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
                as_of_date=end,
                existing_resolutions=existing_resolutions,
            )

            needs_adjustment_recalc = False

            for result in results:
                if result.is_new:
                    self.repository.save_resolution(result.resolution, known_from=known_at)
                    reconciled_count += 1

                    if self.reconciliation_engine.status_allows_adjustment(
                        result.resolution.status
                    ):
                        needs_adjustment_recalc = True

            # 3. If any price-affecting resolution changed, recalculate all factors for this instrument
            if needs_adjustment_recalc:
                current_resolutions = self.repository.load_resolutions(iid)
                new_adjustments = self.adjustment_engine.compute_factors(current_resolutions)

                for adj in new_adjustments:
                    self.repository.save_adjustment(adj, known_from=known_at)
                    adjusted_count += 1

        logger.info(
            f"Reconciliation complete. Generated {reconciled_count} new/updated resolutions and {adjusted_count} adjustment factors."
        )
