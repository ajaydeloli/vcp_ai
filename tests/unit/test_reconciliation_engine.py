"""Unit tests for the Corporate Action Reconciliation Engine (DATA_SPECIFICATION §18A)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.data.reconciliation.engine import (
    ReconciliationConfig,
    ReconciliationEngine,
)
from vcp_scanner.domain.corporate_actions import (
    CorporateAction,
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType


@pytest.fixture
def engine() -> ReconciliationEngine:
    return ReconciliationEngine(ReconciliationConfig(secondary_grace_days=3))


def _make_raw_action(
    source: str,
    action_type: CorporateActionType,
    ex_date: date,
    ratio_num: float = 2.0,
    ratio_den: float = 1.0,
    created_at_date: date | None = None,
) -> CorporateAction:
    if created_at_date is None:
        created_at_date = ex_date - timedelta(days=10)

    return CorporateAction(
        corporate_action_id=f"CA-{source}-{ex_date}",
        instrument_id="TEST",
        action_type=action_type,
        source=source,
        created_at=datetime(
            created_at_date.year,
            created_at_date.month,
            created_at_date.day,
            tzinfo=UTC,
        ),
        ex_date=ex_date,
        ratio_numerator=ratio_num,
        ratio_denominator=ratio_den,
    )


class TestReconciliationEngine:
    def test_both_sources_agree_confirmed(self, engine: ReconciliationEngine) -> None:
        as_of = date(2024, 5, 5)
        ex = date(2024, 5, 10)

        actions = [
            _make_raw_action("NSE", CorporateActionType.SPLIT, ex, 2.0, 1.0),
            _make_raw_action("UPSTOX", CorporateActionType.SPLIT, ex, 2.0, 1.0),
        ]

        results = engine.reconcile("TEST", actions, as_of)

        assert len(results) == 1
        res = results[0].resolution
        assert res.status == CorporateActionStatus.CONFIRMED
        assert res.ratio_numerator == 2.0
        assert res.nse_action_id == f"CA-NSE-{ex}"
        assert res.upstox_action_id == f"CA-UPSTOX-{ex}"
        assert res.conflict_fields is None

    def test_primary_only_within_grace_period_single_source(
        self, engine: ReconciliationEngine
    ) -> None:
        as_of = date(2024, 5, 2)
        ex = date(2024, 5, 10)

        # Created 2 days ago, grace period is 3 days
        created = date(2024, 5, 1)
        actions = [
            _make_raw_action(
                "NSE", CorporateActionType.BONUS, ex, 1.0, 1.0, created_at_date=created
            )
        ]

        results = engine.reconcile("TEST", actions, as_of)

        assert len(results) == 1
        res = results[0].resolution
        assert res.status == CorporateActionStatus.SINGLE_SOURCE
        assert res.upstox_action_id is None

    def test_primary_only_past_grace_period_conflict(self, engine: ReconciliationEngine) -> None:
        as_of = date(2024, 5, 10)
        ex = date(2024, 5, 15)

        # Created 5 days ago, grace period is 3 days
        created = date(2024, 5, 5)
        actions = [
            _make_raw_action(
                "NSE", CorporateActionType.BONUS, ex, 1.0, 1.0, created_at_date=created
            )
        ]

        results = engine.reconcile("TEST", actions, as_of)

        assert len(results) == 1
        res = results[0].resolution
        assert res.status == CorporateActionStatus.PROVIDER_CONFLICT

    def test_secondary_only_conflict(self, engine: ReconciliationEngine) -> None:
        as_of = date(2024, 5, 5)
        ex = date(2024, 5, 10)

        actions = [_make_raw_action("UPSTOX", CorporateActionType.DIVIDEND, ex)]

        results = engine.reconcile("TEST", actions, as_of)

        assert len(results) == 1
        res = results[0].resolution
        assert res.status == CorporateActionStatus.PROVIDER_CONFLICT
        assert res.conflict_fields == "primary_source_missing"

    def test_ratio_conflict(self, engine: ReconciliationEngine) -> None:
        as_of = date(2024, 5, 5)
        ex = date(2024, 5, 10)

        actions = [
            _make_raw_action("NSE", CorporateActionType.SPLIT, ex, 2.0, 1.0),
            _make_raw_action("UPSTOX", CorporateActionType.SPLIT, ex, 5.0, 1.0),
        ]

        results = engine.reconcile("TEST", actions, as_of)

        assert len(results) == 1
        res = results[0].resolution
        assert res.status == CorporateActionStatus.PROVIDER_CONFLICT
        assert "ratio" in res.conflict_fields

    def test_no_change_from_existing_returns_no_result(self, engine: ReconciliationEngine) -> None:
        as_of = date(2024, 5, 5)
        ex = date(2024, 5, 10)

        actions = [
            _make_raw_action("NSE", CorporateActionType.SPLIT, ex, 2.0, 1.0),
            _make_raw_action("UPSTOX", CorporateActionType.SPLIT, ex, 2.0, 1.0),
        ]

        # Suppose we already reconciled this exactly
        existing = CorporateActionResolution(
            resolution_id="OLD-1",
            instrument_id="TEST",
            action_type=CorporateActionType.SPLIT,
            status=CorporateActionStatus.CONFIRMED,
            ex_date=ex,
            ratio_numerator=2.0,
            ratio_denominator=1.0,
            cash_amount=None,
        )

        results = engine.reconcile("TEST", actions, as_of, existing_resolutions=[existing])

        # It shouldn't emit a new resolution if nothing changed
        assert len(results) == 0

    def test_change_from_existing_emits_update(self, engine: ReconciliationEngine) -> None:
        as_of = date(2024, 5, 5)
        ex = date(2024, 5, 10)

        actions = [
            _make_raw_action("NSE", CorporateActionType.SPLIT, ex, 2.0, 1.0),
            _make_raw_action("UPSTOX", CorporateActionType.SPLIT, ex, 2.0, 1.0),
        ]

        # Suppose it used to be SINGLE_SOURCE
        existing = CorporateActionResolution(
            resolution_id="OLD-1",
            instrument_id="TEST",
            action_type=CorporateActionType.SPLIT,
            status=CorporateActionStatus.SINGLE_SOURCE,
            ex_date=ex,
            ratio_numerator=2.0,
            ratio_denominator=1.0,
            cash_amount=None,
        )

        results = engine.reconcile("TEST", actions, as_of, existing_resolutions=[existing])

        assert len(results) == 1
        assert results[0].is_new is False  # signifies it's an update
        assert results[0].resolution.status == CorporateActionStatus.CONFIRMED

    def test_status_allows_adjustment(self) -> None:
        assert (
            ReconciliationEngine.status_allows_adjustment(CorporateActionStatus.CONFIRMED) is True
        )
        assert (
            ReconciliationEngine.status_allows_adjustment(CorporateActionStatus.SINGLE_SOURCE)
            is True
        )
        assert (
            ReconciliationEngine.status_allows_adjustment(CorporateActionStatus.MANUAL_OVERRIDE)
            is True
        )
        assert (
            ReconciliationEngine.status_allows_adjustment(CorporateActionStatus.PROVIDER_CONFLICT)
            is False
        )
