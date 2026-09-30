"""Audit finding P0-2, part 2: the corporate-action worker publishes conflicts to the gate.

Uses real reconciliation (no mocks of the engine): PROVIDER_CONFLICT must block signals from the
ex-date, and a later confirmation must clear the block by itself. Synthetic data only.
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
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import CorporateAction, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType, DataQualityFlag
from vcp_scanner.domain.events import SYSTEM_RESOLVER, EventStatus

IID = "NSE_EQ|CAQ"
EX = date(2024, 5, 10)
T0 = datetime(2024, 5, 1, 9, 0, tzinfo=UTC)
WINDOW = (date(2024, 1, 1), date(2024, 6, 1))
FLAG = DataQualityFlag.CORPORATE_ACTION_UNRESOLVED


def _action(source: str, num: float = 2.0, den: float = 1.0) -> CorporateAction:
    return CorporateAction(
        corporate_action_id=deterministic_action_id(source, IID, "SPLIT", EX, num, den),
        instrument_id=IID,
        action_type=CorporateActionType.SPLIT,
        source=source,
        created_at=T0,
        ex_date=EX,
        ratio_numerator=num,
        ratio_denominator=den,
    )


class _Provider:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self.actions = actions

    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return list(self.actions)


def _env(primary, secondary, *, blocks: bool = True):
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBCorporateActionRepository(store)
    quality = DuckDBDataQualityRepository(store)
    worker = CorporateActionIngestionWorker(
        primary_provider=_Provider(primary),
        secondary_provider=_Provider(secondary),
        repository=repo,
        reconciliation_engine=ReconciliationEngine(ReconciliationConfig(secondary_grace_days=3)),
        adjustment_engine=AdjustmentEngine(),
        quality_repository=quality,
        conflict_blocks_signals=blocks,
    )
    return worker, repo, quality


def test_provider_conflict_blocks_signals_from_the_ex_date() -> None:
    worker, repo, quality = _env([_action("NSE", 10, 2)], [_action("UPSTOX", 4, 1)])
    worker.run(*WINDOW, known_at=T0)

    assert repo.load_resolutions(IID)[0].status is CorporateActionStatus.PROVIDER_CONFLICT
    assert quality.blocked_instruments([IID], EX) == {IID: (FLAG.value,)}
    assert quality.blocked_instruments([IID], EX - timedelta(days=1)) == {}  # before ex-date
    (event,) = quality.load_events()
    assert event.dataset == "corporate_actions" and event.trade_date == EX


def test_standing_conflict_is_not_duplicated_on_rerun() -> None:
    worker, _, quality = _env([_action("NSE", 10, 2)], [_action("UPSTOX", 4, 1)])
    worker.run(*WINDOW, known_at=T0)
    worker.run(*WINDOW, known_at=T0 + timedelta(days=1))
    assert len(quality.load_events(open_only=True)) == 1


def test_single_source_within_grace_does_not_block() -> None:
    worker, _, quality = _env([_action("NSE")], [])
    worker.run(*WINDOW, known_at=T0)
    assert quality.load_events() == []
    assert quality.blocked_instruments([IID], EX) == {}


def test_grace_expiry_blocks_and_a_later_confirmation_clears_it() -> None:
    worker, repo, quality = _env([_action("NSE")], [])
    worker.run(*WINDOW, known_at=T0)
    worker.run(*WINDOW, known_at=T0 + timedelta(days=10))  # NSE-only past the grace period
    assert quality.blocked_instruments([IID], EX) == {IID: (FLAG.value,)}

    # Upstox now agrees: the resolution supersedes to CONFIRMED and the block lifts on its own.
    worker.secondary_provider = _Provider([_action("UPSTOX")])
    worker.run(*WINDOW, known_at=T0 + timedelta(days=11))

    assert repo.load_resolutions(IID)[0].status is CorporateActionStatus.CONFIRMED
    assert quality.blocked_instruments([IID], EX) == {}
    (event,) = quality.load_events()
    assert event.status is EventStatus.RESOLVED and event.resolved_by == SYSTEM_RESOLVER


def test_conflict_blocking_can_be_switched_off_by_config() -> None:
    worker, _, quality = _env([_action("NSE", 10, 2)], [_action("UPSTOX", 4, 1)], blocks=False)
    worker.run(*WINDOW, known_at=T0)
    (event,) = quality.load_events()
    assert event.blocks_signal is False  # still recorded and visible...
    assert quality.blocked_instruments([IID], EX) == {}  # ...but it does not gate


def test_worker_without_repository_still_works() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    worker = CorporateActionIngestionWorker(
        primary_provider=_Provider([_action("NSE", 10, 2)]),
        secondary_provider=_Provider([_action("UPSTOX", 4, 1)]),
        repository=DuckDBCorporateActionRepository(store),
        reconciliation_engine=ReconciliationEngine(),
        adjustment_engine=AdjustmentEngine(),
    )
    worker.run(*WINDOW, known_at=T0)
    assert store.conn.execute("SELECT COUNT(*) FROM data_quality_events").fetchone() == (0,)
