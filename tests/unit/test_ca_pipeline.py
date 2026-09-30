"""Corporate-action pipeline behaviour fixed after the Phase 1-5 review.

Covers: resolution updates are persisted, PROVIDER_CONFLICT never feeds factors, retired
factors are closed, equivalent ratios reconcile, ingestion is idempotent, first-seen time
survives a round trip, and the grace period is judged from ingestion time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.identity import deterministic_action_id
from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
from vcp_scanner.data.reconciliation.engine import ReconciliationConfig, ReconciliationEngine
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import (
    CorporateAction,
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType

IID = "NSE_EQ|TEST"
EX = date(2024, 5, 10)
T0 = datetime(2024, 5, 1, 9, 0, tzinfo=UTC)
WINDOW = (date(2024, 1, 1), date(2024, 6, 1))


def _action(source: str, num: float = 2.0, den: float = 1.0, ex: date = EX) -> CorporateAction:
    return CorporateAction(
        # Deterministic, like the real providers: same observation -> same ID.
        corporate_action_id=deterministic_action_id(source, IID, "SPLIT", ex, num, den),
        instrument_id=IID,
        action_type=CorporateActionType.SPLIT,
        source=source,
        created_at=T0,
        ex_date=ex,
        ratio_numerator=num,
        ratio_denominator=den,
    )


def _resolution(status: CorporateActionStatus) -> CorporateActionResolution:
    return CorporateActionResolution(
        resolution_id=f"R-{status}",
        instrument_id=IID,
        action_type=CorporateActionType.SPLIT,
        status=status,
        ex_date=EX,
        ratio_numerator=2.0,
        ratio_denominator=1.0,
    )


class _FakeProvider:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self.actions = actions

    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return list(self.actions)


def _worker(primary: list[CorporateAction], secondary: list[CorporateAction]):
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBCorporateActionRepository(store)
    worker = CorporateActionIngestionWorker(
        primary_provider=_FakeProvider(primary),
        secondary_provider=_FakeProvider(secondary),
        repository=repo,
        reconciliation_engine=ReconciliationEngine(ReconciliationConfig(secondary_grace_days=3)),
        adjustment_engine=AdjustmentEngine(),
    )
    return worker, repo, store


def _count(store: DuckDBStore, sql: str) -> int:
    return int(store.conn.execute(sql).fetchone()[0])


class TestReconciliationRatios:
    def test_equivalent_ratios_confirm(self) -> None:
        # NSE reports 10:2, Upstox reports 5:1 - the same split.
        engine = ReconciliationEngine()
        results = engine.reconcile(
            IID, [_action("NSE", 10.0, 2.0), _action("UPSTOX", 5.0, 1.0)], date(2024, 5, 5)
        )
        assert results[0].resolution.status == CorporateActionStatus.CONFIRMED

    def test_different_ratios_still_conflict(self) -> None:
        engine = ReconciliationEngine()
        results = engine.reconcile(
            IID, [_action("NSE", 10.0, 2.0), _action("UPSTOX", 4.0, 1.0)], date(2024, 5, 5)
        )
        res = results[0].resolution
        assert res.status == CorporateActionStatus.PROVIDER_CONFLICT
        assert res.conflict_fields == "ratio"


class TestAdjustmentStatusFilter:
    def test_provider_conflict_produces_no_factors(self) -> None:
        engine = AdjustmentEngine()
        assert engine.compute_factors([_resolution(CorporateActionStatus.PROVIDER_CONFLICT)]) == []

    def test_usable_statuses_produce_factors(self) -> None:
        engine = AdjustmentEngine()
        for status in (
            CorporateActionStatus.CONFIRMED,
            CorporateActionStatus.SINGLE_SOURCE,
            CorporateActionStatus.MANUAL_OVERRIDE,
        ):
            factors = engine.compute_factors([_resolution(status)])
            assert len(factors) == 1
            assert factors[0].price_factor == 0.5


class TestRepositoryIdempotency:
    def test_same_observation_saved_once_and_first_seen_kept(self) -> None:
        _, repo, store = _worker([], [])
        action = _action("NSE")
        assert repo.save_corporate_action(action, known_from=T0) is True
        assert repo.save_corporate_action(action, known_from=T0 + timedelta(days=9)) is False

        assert _count(store, "SELECT COUNT(*) FROM corporate_actions") == 1
        loaded = repo.load_corporate_actions(IID)
        assert loaded[0].ingested_at == T0  # first-seen survives the re-save

    def test_deterministic_id_is_stable_and_source_specific(self) -> None:
        a = deterministic_action_id("NSE", "INE1", "SPLIT", EX, 2.0, 1.0)
        assert a == deterministic_action_id("NSE", "INE1", "SPLIT", EX, 2.0, 1.0)
        assert a != deterministic_action_id("UPSTOX", "INE1", "SPLIT", EX, 2.0, 1.0)
        assert a != deterministic_action_id("NSE", "INE1", "SPLIT", EX, 5.0, 1.0)


class TestWorker:
    def test_rerun_does_not_duplicate_rows(self) -> None:
        worker, _, store = _worker([_action("NSE")], [_action("UPSTOX")])
        worker.run(*WINDOW, known_at=T0)
        worker.run(*WINDOW, known_at=T0 + timedelta(hours=1))

        assert _count(store, "SELECT COUNT(*) FROM corporate_actions") == 2
        current = "SELECT COUNT(*) FROM corporate_action_resolution WHERE known_to IS NULL"
        assert _count(store, current) == 1

    def test_single_source_upgrade_to_confirmed_is_persisted(self) -> None:
        worker, repo, _ = _worker([_action("NSE")], [])
        worker.run(*WINDOW, known_at=T0)
        assert repo.load_resolutions(IID)[0].status == CorporateActionStatus.SINGLE_SOURCE

        # Upstox now reports the same action.
        worker.secondary_provider = _FakeProvider([_action("UPSTOX")])
        worker.run(*WINDOW, known_at=T0 + timedelta(days=1))

        current = repo.load_resolutions(IID)
        assert len(current) == 1
        assert current[0].status == CorporateActionStatus.CONFIRMED
        assert len(repo.load_adjustments(IID)) == 1

    def test_grace_period_expiry_becomes_conflict_and_retires_factors(self) -> None:
        worker, repo, store = _worker([_action("NSE")], [])
        # Upstox is configured and was asked about this instrument, but never reports it.
        worker.secondary_provider.queried_instrument_ids = {IID}
        worker.run(*WINDOW, known_at=T0)
        assert len(repo.load_adjustments(IID)) == 1

        # Still only NSE, 10 days after first sight: past secondary_grace_days=3.
        worker.run(*WINDOW, known_at=T0 + timedelta(days=10))

        assert repo.load_resolutions(IID)[0].status == CorporateActionStatus.PROVIDER_CONFLICT
        # The old factor is retired, not left silently active.
        assert repo.load_adjustments(IID) == []
        closed = "SELECT COUNT(*) FROM corporate_action_adjustments WHERE known_to IS NOT NULL"
        assert _count(store, closed) == 1

    def test_grace_expiry_without_secondary_coverage_keeps_the_factor(self) -> None:
        """Audit P0-2 policy: with no Upstox coverage an NSE-only split stays SINGLE_SOURCE."""
        worker, repo, _ = _worker([_action("NSE")], [])  # fake reports no coverage
        worker.run(*WINDOW, known_at=T0)
        worker.run(*WINDOW, known_at=T0 + timedelta(days=10))
        assert repo.load_resolutions(IID)[0].status == CorporateActionStatus.SINGLE_SOURCE
        assert len(repo.load_adjustments(IID)) == 1

    def test_grace_period_uses_ingestion_time_not_window_end(self) -> None:
        # A backfill window that ended long ago must not make a just-seen action stale.
        worker, repo, _ = _worker([_action("NSE", ex=date(2022, 3, 1))], [])
        worker.run(date(2022, 1, 1), date(2022, 6, 1), known_at=T0)
        assert repo.load_resolutions(IID)[0].status == CorporateActionStatus.SINGLE_SOURCE

    def test_unchanged_conflict_is_not_rewritten_on_rerun(self) -> None:
        worker, repo, store = _worker([_action("NSE", 10, 2)], [_action("UPSTOX", 4, 1)])
        worker.run(*WINDOW, known_at=T0)
        worker.run(*WINDOW, known_at=T0 + timedelta(days=1))
        assert _count(store, "SELECT COUNT(*) FROM corporate_action_resolution") == 1
        assert repo.load_adjustments(IID) == []
