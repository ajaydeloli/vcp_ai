"""Unit tests for DuckDBCorporateActionRepository.

Validates bitemporal CRUD for all three corporate action tables:
corporate_actions, corporate_action_resolution, corporate_action_adjustments.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import (
    CorporateAction,
    CorporateActionAdjustment,
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo() -> DuckDBCorporateActionRepository:
    store = DuckDBStore(":memory:")
    store.migrate()
    return DuckDBCorporateActionRepository(store)


def _make_action(
    *,
    ca_id: str = "CA-1",
    instrument_id: str = "RELIANCE",
    action_type: CorporateActionType = CorporateActionType.SPLIT,
    source: str = "NSE",
    ex_date: date | None = date(2024, 5, 1),
    ratio_num: float = 2.0,
    ratio_den: float = 1.0,
) -> CorporateAction:
    return CorporateAction(
        corporate_action_id=ca_id,
        instrument_id=instrument_id,
        action_type=action_type,
        source=source,
        created_at=datetime(2024, 4, 15, 8, 0, tzinfo=UTC),
        ex_date=ex_date,
        ratio_numerator=ratio_num,
        ratio_denominator=ratio_den,
    )


def _make_resolution(
    *,
    res_id: str = "RES-1",
    instrument_id: str = "RELIANCE",
    action_type: CorporateActionType = CorporateActionType.SPLIT,
    status: CorporateActionStatus = CorporateActionStatus.CONFIRMED,
    ex_date: date | None = date(2024, 5, 1),
    ratio_num: float = 2.0,
    ratio_den: float = 1.0,
) -> CorporateActionResolution:
    return CorporateActionResolution(
        resolution_id=res_id,
        instrument_id=instrument_id,
        action_type=action_type,
        status=status,
        ex_date=ex_date,
        ratio_numerator=ratio_num,
        ratio_denominator=ratio_den,
    )


def _make_adjustment(
    *,
    res_id: str = "RES-1",
    instrument_id: str = "RELIANCE",
    effective_date: date = date(2024, 5, 1),
    price_factor: float = 0.5,
    volume_factor: float = 2.0,
) -> CorporateActionAdjustment:
    return CorporateActionAdjustment(
        resolution_id=res_id,
        instrument_id=instrument_id,
        effective_date=effective_date,
        price_factor=price_factor,
        volume_factor=volume_factor,
        cumulative_price_factor=price_factor,
        cumulative_volume_factor=volume_factor,
        source="NSE",
        calculation_version="1.0",
    )


# ---------------------------------------------------------------------------
# Raw corporate_actions tests
# ---------------------------------------------------------------------------


class TestSaveCorporateAction:
    def test_save_and_load(self, repo: DuckDBCorporateActionRepository) -> None:
        action = _make_action()
        t1 = datetime(2024, 4, 15, 8, 0, tzinfo=UTC)
        repo.save_corporate_action(action, known_from=t1)

        loaded = repo.load_corporate_actions("RELIANCE")
        assert len(loaded) == 1
        assert loaded[0].corporate_action_id == "CA-1"
        assert loaded[0].action_type == CorporateActionType.SPLIT
        assert loaded[0].ratio_numerator == 2.0

    def test_multiple_sources(self, repo: DuckDBCorporateActionRepository) -> None:
        t1 = datetime(2024, 4, 15, 8, 0, tzinfo=UTC)
        nse = _make_action(ca_id="CA-NSE", source="NSE")
        upstox = _make_action(ca_id="CA-UPSTOX", source="UPSTOX")
        repo.save_corporate_action(nse, known_from=t1)
        repo.save_corporate_action(upstox, known_from=t1)

        loaded = repo.load_corporate_actions("RELIANCE")
        assert len(loaded) == 2
        sources = {a.source for a in loaded}
        assert sources == {"NSE", "UPSTOX"}

    def test_point_in_time_read(self, repo: DuckDBCorporateActionRepository) -> None:
        """Actions inserted after known_at should not be visible."""
        t1 = datetime(2024, 4, 15, 8, 0, tzinfo=UTC)
        t2 = datetime(2024, 4, 20, 8, 0, tzinfo=UTC)

        action = _make_action()
        repo.save_corporate_action(action, known_from=t2)

        # Query as of before t2 — should be empty
        loaded_before = repo.load_corporate_actions("RELIANCE", known_at=t1)
        assert len(loaded_before) == 0

        # Query as of t2 — should be visible
        loaded_at = repo.load_corporate_actions("RELIANCE", known_at=t2)
        assert len(loaded_at) == 1


# ---------------------------------------------------------------------------
# Resolution tests
# ---------------------------------------------------------------------------


class TestResolution:
    def test_save_and_load(self, repo: DuckDBCorporateActionRepository) -> None:
        res = _make_resolution()
        t1 = datetime(2024, 4, 15, 10, 0, tzinfo=UTC)
        repo.save_resolution(res, known_from=t1)

        loaded = repo.load_resolutions("RELIANCE")
        assert len(loaded) == 1
        assert loaded[0].status == CorporateActionStatus.CONFIRMED
        assert loaded[0].ratio_numerator == 2.0

    def test_supersede_closes_old_row(self, repo: DuckDBCorporateActionRepository) -> None:
        """Saving a new resolution for the same logical key closes the
        prior row's known_to."""
        t1 = datetime(2024, 4, 15, 10, 0, tzinfo=UTC)
        t2 = datetime(2024, 4, 16, 10, 0, tzinfo=UTC)

        res_v1 = _make_resolution(
            res_id="RES-V1",
            status=CorporateActionStatus.SINGLE_SOURCE,
        )
        repo.save_resolution(res_v1, known_from=t1)

        # Now a CONFIRMED resolution supersedes it
        res_v2 = _make_resolution(
            res_id="RES-V2",
            status=CorporateActionStatus.CONFIRMED,
        )
        repo.save_resolution(res_v2, known_from=t2)

        # Current read should see only the new one
        current = repo.load_resolutions("RELIANCE")
        assert len(current) == 1
        assert current[0].resolution_id == "RES-V2"
        assert current[0].status == CorporateActionStatus.CONFIRMED

        # Point-in-time read at t1 should see the old one
        as_of_t1 = repo.load_resolutions("RELIANCE", known_at=t1)
        assert len(as_of_t1) == 1
        assert as_of_t1[0].resolution_id == "RES-V1"
        assert as_of_t1[0].status == CorporateActionStatus.SINGLE_SOURCE

    def test_provider_conflict_status(self, repo: DuckDBCorporateActionRepository) -> None:
        res = _make_resolution(
            status=CorporateActionStatus.PROVIDER_CONFLICT,
        )
        t1 = datetime(2024, 4, 15, 10, 0, tzinfo=UTC)
        repo.save_resolution(res, known_from=t1)

        loaded = repo.load_resolutions("RELIANCE")
        assert loaded[0].status == CorporateActionStatus.PROVIDER_CONFLICT


# ---------------------------------------------------------------------------
# Adjustment factor tests
# ---------------------------------------------------------------------------


class TestAdjustment:
    def test_save_and_load(self, repo: DuckDBCorporateActionRepository) -> None:
        adj = _make_adjustment()
        t1 = datetime(2024, 5, 1, 6, 0, tzinfo=UTC)
        repo.save_adjustment(adj, known_from=t1)

        loaded = repo.load_adjustments("RELIANCE")
        assert len(loaded) == 1
        assert loaded[0].price_factor == 0.5
        assert loaded[0].volume_factor == 2.0
        assert loaded[0].cumulative_price_factor == 0.5

    def test_multiple_adjustments_ordered(self, repo: DuckDBCorporateActionRepository) -> None:
        t1 = datetime(2024, 5, 1, 6, 0, tzinfo=UTC)

        adj1 = _make_adjustment(
            res_id="RES-1",
            effective_date=date(2024, 5, 1),
            price_factor=0.5,
            volume_factor=2.0,
        )
        adj2 = _make_adjustment(
            res_id="RES-2",
            effective_date=date(2024, 8, 1),
            price_factor=0.2,
            volume_factor=5.0,
        )
        repo.save_adjustment(adj1, known_from=t1)
        repo.save_adjustment(adj2, known_from=t1)

        loaded = repo.load_adjustments("RELIANCE")
        assert len(loaded) == 2
        # Should be ordered by effective_date
        assert loaded[0].effective_date < loaded[1].effective_date

    def test_point_in_time_adjustment(self, repo: DuckDBCorporateActionRepository) -> None:
        t1 = datetime(2024, 5, 1, 6, 0, tzinfo=UTC)
        t2 = datetime(2024, 8, 1, 6, 0, tzinfo=UTC)

        adj = _make_adjustment()
        repo.save_adjustment(adj, known_from=t2)

        # Before t2: should be empty
        loaded_before = repo.load_adjustments("RELIANCE", known_at=t1)
        assert len(loaded_before) == 0

        # At t2: should be visible
        loaded_at = repo.load_adjustments("RELIANCE", known_at=t2)
        assert len(loaded_at) == 1


# ---------------------------------------------------------------------------
# Cross-table integration: realistic workflow
# ---------------------------------------------------------------------------


class TestFullWorkflow:
    def test_nse_split_ingestion_to_adjustment(self, repo: DuckDBCorporateActionRepository) -> None:
        """Simulate a full split lifecycle: raw → resolution → adjustment."""
        t1 = datetime(2024, 4, 15, 6, 0, tzinfo=UTC)

        # 1. Raw observation from NSE
        nse_action = _make_action(ca_id="CA-NSE", source="NSE")
        repo.save_corporate_action(nse_action, known_from=t1)

        # 2. Raw observation from Upstox (same split)
        upstox_action = _make_action(ca_id="CA-UPSTOX", source="UPSTOX")
        repo.save_corporate_action(upstox_action, known_from=t1)

        # 3. Both agree → CONFIRMED resolution
        resolution = _make_resolution(
            res_id="RES-CONFIRMED",
            status=CorporateActionStatus.CONFIRMED,
        )
        repo.save_resolution(resolution, known_from=t1)

        # 4. Adjustment factor derived from the confirmed resolution
        adj = _make_adjustment(res_id="RES-CONFIRMED")
        repo.save_adjustment(adj, known_from=t1)

        # Verify the full chain
        actions = repo.load_corporate_actions("RELIANCE")
        assert len(actions) == 2

        resolutions = repo.load_resolutions("RELIANCE")
        assert len(resolutions) == 1
        assert resolutions[0].status == CorporateActionStatus.CONFIRMED

        adjustments = repo.load_adjustments("RELIANCE")
        assert len(adjustments) == 1
        assert adjustments[0].price_factor == 0.5
