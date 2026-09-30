"""Corporate Action Reconciliation Engine (DATA_SPECIFICATION §18A).

Implements the cross-source reconciliation rules:
- Both sources agree on (instrument, action_type, ex_date, ratio, cash)
  → CONFIRMED
- Only primary (NSE) has it, within secondary_grace_days
  → SINGLE_SOURCE
- Sources disagree on ex_date, type, ratio, or (when both report one) cash amount;
  or only secondary has it
  → PROVIDER_CONFLICT
- Only primary has a split/bonus past the grace period AND the secondary source was
  actually queried for this instrument over a window containing the ex-date
  → PROVIDER_CONFLICT. Otherwise (secondary not configured / not queried, or a
  dividend/rights action) it stays SINGLE_SOURCE (audit P0-2 policy, 2026-09-30).
- Only split/bonus conflicts block signals; dividend/rights conflicts are warnings
  (``data.quality.events``).
- Human override recorded
  → MANUAL_OVERRIDE

Only CONFIRMED, SINGLE_SOURCE, and MANUAL_OVERRIDE feed adjustment factors.
A PROVIDER_CONFLICT blocks signals for that symbol until superseded.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date

from vcp_scanner.domain.corporate_actions import (
    PRICE_SCALING_ACTIONS,
    CorporateAction,
    CorporateActionResolution,
    CorporateActionStatus,
    status_allows_adjustment,
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
        *,
        secondary_window: tuple[date, date] | None = None,
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
            secondary_window: ``(start, end)`` ex-date window over which the secondary
                source was actually queried for this instrument in this run, or ``None``
                when it was not queried (not configured, no ISIN). Only then can the
                secondary's silence about a primary-only split/bonus count as a conflict.

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
                secondary_window=secondary_window,
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
        secondary_window: tuple[date, date] | None = None,
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
            # Only primary — grace period, then escalate only if the secondary was asked
            status = self._check_grace_period(
                primary_actions[0], as_of_date, secondary_window=secondary_window
            )
            conflict_fields = (
                "secondary_source_missing"
                if status is CorporateActionStatus.PROVIDER_CONFLICT
                else None
            )
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

        if not self._ratios_equivalent(primary, secondary):
            conflicts.append("ratio")

        if not self._cash_equivalent(primary.cash_amount, secondary.cash_amount):
            conflicts.append("cash_amount")

        if conflicts:
            return (
                CorporateActionStatus.PROVIDER_CONFLICT,
                ",".join(conflicts),
            )

        return CorporateActionStatus.CONFIRMED, None

    @staticmethod
    def _cash_equivalent(a: float | None, b: float | None) -> bool:
        """Cash amounts disagree only when both sources report one and they differ.

        NSE's feed carries no parsed amount; treating its ``None`` as a disagreement made every
        dividend reported by both sources a PROVIDER_CONFLICT (audit P0-2). Half a paisa
        absorbs float formatting.
        """
        if a is None or b is None:
            return True
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=0.005)

    @staticmethod
    def _ratios_equivalent(a: CorporateAction, b: CorporateAction) -> bool:
        """Compare ratios by value, not by spelling: NSE's 10:2 and Upstox's 5:1 agree.

        Cross-multiplication avoids dividing by zero. Both sides missing a ratio agree;
        exactly one side missing does not.
        """
        pairs = (
            (a.ratio_numerator, a.ratio_denominator),
            (b.ratio_numerator, b.ratio_denominator),
        )
        a_missing = a.ratio_numerator is None or a.ratio_denominator is None
        b_missing = b.ratio_numerator is None or b.ratio_denominator is None
        if a_missing or b_missing:
            return a_missing and b_missing and pairs[0] == pairs[1]
        assert a.ratio_numerator is not None and a.ratio_denominator is not None
        assert b.ratio_numerator is not None and b.ratio_denominator is not None
        return math.isclose(
            a.ratio_numerator * b.ratio_denominator,
            b.ratio_numerator * a.ratio_denominator,
            rel_tol=1e-9,
            abs_tol=1e-12,
        )

    def _check_grace_period(
        self,
        action: CorporateAction,
        as_of_date: date,
        *,
        secondary_window: tuple[date, date] | None = None,
    ) -> CorporateActionStatus:
        """Determine status when only the primary source has the action.

        Within secondary_grace_days of first sighting → SINGLE_SOURCE.
        Past the grace period → PROVIDER_CONFLICT only for a split/bonus whose ex-date lies
        in ``secondary_window`` (the secondary was asked and stayed silent). Otherwise
        SINGLE_SOURCE: an unasked source is not evidence, and a dividend/rights action does
        not rescale prices (audit P0-2 policy).

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
        secondary_was_asked = (
            secondary_window is not None
            and action.ex_date is not None
            and secondary_window[0] <= action.ex_date <= secondary_window[1]
        )
        if action.action_type in PRICE_SCALING_ACTIONS and secondary_was_asked:
            return CorporateActionStatus.PROVIDER_CONFLICT
        return CorporateActionStatus.SINGLE_SOURCE

    @staticmethod
    def status_allows_adjustment(
        status: CorporateActionStatus,
    ) -> bool:
        """Whether a resolution status permits adjustment factor generation.

        Per DATABASE_SCHEMA §17A: only CONFIRMED, SINGLE_SOURCE, and
        MANUAL_OVERRIDE feed corporate_action_adjustments.
        """
        return status_allows_adjustment(status)
