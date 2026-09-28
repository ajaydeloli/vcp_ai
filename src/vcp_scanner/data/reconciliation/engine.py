"""Corporate Action Reconciliation Engine (DATA_SPECIFICATION §18A).

Implements the cross-source reconciliation rules:
- Both sources agree on (instrument, action_type, ex_date, ratio, cash)
  → CONFIRMED
- Only primary (NSE) has it, within secondary_grace_days
  → SINGLE_SOURCE
- Sources disagree on ex_date, type, or ratio; or only secondary has it;
  or primary-only past grace period
  → PROVIDER_CONFLICT (signals blocked)
- Human override recorded
  → MANUAL_OVERRIDE

Only CONFIRMED, SINGLE_SOURCE, and MANUAL_OVERRIDE feed adjustment factors.
A PROVIDER_CONFLICT blocks signals for that symbol until superseded.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date

from vcp_scanner.domain.corporate_actions import (
    CorporateAction,
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Output of reconciling one logical corporate action."""

    resolution: CorporateActionResolution
    is_new: bool  # True if this is a new resolution, not an update


@dataclass(frozen=True, slots=True)
class ReconciliationConfig:
    """Configuration for the reconciliation engine."""

    primary_source: str = "NSE"
    secondary_source: str = "UPSTOX"
    secondary_grace_days: int = 3
    conflict_blocks_signals: bool = True


class ReconciliationEngine:
    """Cross-source corporate action reconciliation.

    Takes raw CorporateAction observations from multiple sources and
    produces CorporateActionResolution rows per the rules in §18A.
    """

    def __init__(self, config: ReconciliationConfig | None = None) -> None:
        self._config = config or ReconciliationConfig()

    def reconcile(
        self,
        instrument_id: str,
        actions: list[CorporateAction],
        as_of_date: date,
        existing_resolutions: list[CorporateActionResolution] | None = None,
    ) -> list[ReconciliationResult]:
        """Reconcile raw actions for one instrument.

        Groups actions by (action_type, ex_date) and determines the
        reconciliation status for each group.

        Args:
            instrument_id: The instrument to reconcile.
            actions: All current raw corporate action observations.
            as_of_date: The reference date for grace-period calculation.
            existing_resolutions: Already-stored resolutions to detect
                changes vs new entries.

        Returns:
            List of ReconciliationResult, one per distinct
            (action_type, ex_date) group.
        """
        # Group actions by logical key: (action_type, ex_date)
        groups: dict[
            tuple[CorporateActionType, date | None],
            list[CorporateAction],
        ] = {}
        for action in actions:
            if action.instrument_id != instrument_id:
                continue
            key = (action.action_type, action.ex_date)
            groups.setdefault(key, []).append(action)

        # Index existing resolutions by the same key
        existing_by_key: dict[
            tuple[CorporateActionType, date | None],
            CorporateActionResolution,
        ] = {}
        for res in existing_resolutions or []:
            existing_by_key[(res.action_type, res.ex_date)] = res

        results: list[ReconciliationResult] = []
        for (action_type, ex_date), group_actions in groups.items():
            result = self._reconcile_group(
                instrument_id=instrument_id,
                action_type=action_type,
                ex_date=ex_date,
                group_actions=group_actions,
                as_of_date=as_of_date,
                existing=existing_by_key.get((action_type, ex_date)),
            )
            if result is not None:
                results.append(result)

        return results

    def _reconcile_group(
        self,
        instrument_id: str,
        action_type: CorporateActionType,
        ex_date: date | None,
        group_actions: list[CorporateAction],
        as_of_date: date,
        existing: CorporateActionResolution | None,
    ) -> ReconciliationResult | None:
        """Reconcile a single (action_type, ex_date) group."""
        primary = self._config.primary_source.upper()
        secondary = self._config.secondary_source.upper()

        by_source: dict[str, list[CorporateAction]] = {}
        for a in group_actions:
            by_source.setdefault(a.source.upper(), []).append(a)

        primary_actions = by_source.get(primary, [])
        secondary_actions = by_source.get(secondary, [])

        has_primary = len(primary_actions) > 0
        has_secondary = len(secondary_actions) > 0

        if has_primary and has_secondary:
            # Both sources present — check agreement
            status, conflict_fields = self._check_agreement(
                primary_actions[0], secondary_actions[0]
            )
        elif has_primary and not has_secondary:
            # Only primary — check grace period
            status = self._check_grace_period(primary_actions[0], as_of_date)
            conflict_fields = None
        elif has_secondary and not has_primary:
            # Only secondary has it — conflict per §18A
            status = CorporateActionStatus.PROVIDER_CONFLICT
            conflict_fields = "primary_source_missing"
        else:
            return None  # No actions in this group

        # Pick the primary action values if available, else secondary
        reference = primary_actions[0] if has_primary else secondary_actions[0]

        nse_id = primary_actions[0].corporate_action_id if has_primary else None
        upstox_id = secondary_actions[0].corporate_action_id if has_secondary else None

        resolution = CorporateActionResolution(
            resolution_id=str(uuid.uuid4()),
            instrument_id=instrument_id,
            action_type=action_type,
            status=status,
            isin=reference.isin,
            ex_date=ex_date,
            ratio_numerator=reference.ratio_numerator,
            ratio_denominator=reference.ratio_denominator,
            cash_amount=reference.cash_amount,
            nse_action_id=nse_id,
            upstox_action_id=upstox_id,
            conflict_fields=conflict_fields,
        )

        # Determine if this is genuinely new or just matches existing
        is_new = True
        if existing is not None:
            if (
                existing.status == status
                and existing.ratio_numerator == resolution.ratio_numerator
                and existing.ratio_denominator == resolution.ratio_denominator
                and existing.cash_amount == resolution.cash_amount
            ):
                # No change — skip
                return None
            is_new = False  # It's an update to an existing resolution

        return ReconciliationResult(resolution=resolution, is_new=is_new)

    def _check_agreement(
        self,
        primary: CorporateAction,
        secondary: CorporateAction,
    ) -> tuple[CorporateActionStatus, str | None]:
        """Compare primary and secondary source observations.

        Returns (status, conflict_fields_csv_or_none).
        """
        conflicts: list[str] = []

        if primary.ex_date != secondary.ex_date:
            conflicts.append("ex_date")

        if primary.action_type != secondary.action_type:
            conflicts.append("action_type")

        if (
            primary.ratio_numerator != secondary.ratio_numerator
            or primary.ratio_denominator != secondary.ratio_denominator
        ):
            conflicts.append("ratio")

        if primary.cash_amount != secondary.cash_amount:
            conflicts.append("cash_amount")

        if conflicts:
            return (
                CorporateActionStatus.PROVIDER_CONFLICT,
                ",".join(conflicts),
            )

        return CorporateActionStatus.CONFIRMED, None

    def _check_grace_period(
        self,
        action: CorporateAction,
        as_of_date: date,
    ) -> CorporateActionStatus:
        """Determine status when only the primary source has the action.

        Within secondary_grace_days of first sighting → SINGLE_SOURCE.
        Past grace period → PROVIDER_CONFLICT.

        Uses ``ingested_at`` (when we first saw the action) rather than the
        provider's ``created_at``, to avoid premature conflict when the
        provider publishes late-dated records.  Falls back to ``created_at``
        if ``ingested_at`` is not set.
        """
        # Prefer our own ingestion timestamp over the provider's
        first_seen_ts = action.ingested_at or action.created_at
        first_seen = first_seen_ts.date()
        days_since = (as_of_date - first_seen).days

        if days_since <= self._config.secondary_grace_days:
            return CorporateActionStatus.SINGLE_SOURCE
        else:
            return CorporateActionStatus.PROVIDER_CONFLICT

    @staticmethod
    def status_allows_adjustment(
        status: CorporateActionStatus,
    ) -> bool:
        """Whether a resolution status permits adjustment factor generation.

        Per DATABASE_SCHEMA §17A: only CONFIRMED, SINGLE_SOURCE, and
        MANUAL_OVERRIDE feed corporate_action_adjustments.
        """
        return status in (
            CorporateActionStatus.CONFIRMED,
            CorporateActionStatus.SINGLE_SOURCE,
            CorporateActionStatus.MANUAL_OVERRIDE,
        )
