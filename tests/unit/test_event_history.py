"""Audit P1-2a (C5): the point-in-time gate reads each event's state history."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity

IID = "NSE_EQ|HIST"
FLAG = DataQualityFlag.UNEXPLAINED_GAP
T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)
AS_OF = date(2026, 6, 1)


def _event(blocks: bool = True) -> DataQualityEvent:
    return DataQualityEvent(
        event_id="dq-hist",
        instrument_id=IID,
        flag=FLAG,
        severity=EventSeverity.HIGH,
        detected_at=T0,
        description="synthetic gap",
        trade_date=date(2025, 12, 31),
        blocks_signal=blocks,
    )


@pytest.fixture
def repo() -> DuckDBDataQualityRepository:
    store = DuckDBStore(":memory:")
    store.migrate()
    return DuckDBDataQualityRepository(store)


def _blocked(repo: DuckDBDataQualityRepository, at: datetime) -> bool:
    return IID in repo.blocked_instruments([IID], AS_OF, known_at=at)


def test_a_reopened_event_keeps_its_unblocked_interval(repo) -> None:
    """Open day 0, cleared day 10, back day 20: the gate as known on day 15 is unblocked.
    Before C5 the reopen wiped resolved_at and day 15 read as blocked."""
    repo.sync_events(IID, FLAG, [_event()], at=T0)
    repo.sync_events(IID, FLAG, [], at=T0 + timedelta(days=10))
    repo.sync_events(IID, FLAG, [_event()], at=T0 + timedelta(days=20))
    assert _blocked(repo, T0 + timedelta(days=5))
    assert not _blocked(repo, T0 + timedelta(days=15))
    assert _blocked(repo, T0 + timedelta(days=25))
    assert not _blocked(repo, T0 - timedelta(days=1))  # before detection
    assert IID in repo.blocked_instruments([IID], AS_OF)  # current state


def test_a_change_of_blocks_signal_is_dated(repo) -> None:
    repo.sync_events(IID, FLAG, [_event(blocks=False)], at=T0)
    repo.sync_events(IID, FLAG, [_event(blocks=True)], at=T0 + timedelta(days=10))
    assert not _blocked(repo, T0 + timedelta(days=5))
    assert _blocked(repo, T0 + timedelta(days=15))


def test_a_human_resolution_is_dated_and_refresh_does_not_reopen_it(repo) -> None:
    repo.sync_events(IID, FLAG, [_event()], at=T0)
    assert repo.resolve(
        "dq-hist", resolved_by="Ajay", note="genuine", resolved_at=T0 + timedelta(days=3)
    )
    repo.sync_events(IID, FLAG, [_event()], at=T0 + timedelta(days=4))  # detector sees it again
    assert _blocked(repo, T0 + timedelta(days=2))
    assert not _blocked(repo, T0 + timedelta(days=5))
    rows = repo._store.conn.execute(
        "SELECT status, resolved_by FROM data_quality_event_history ORDER BY valid_from"
    ).fetchall()
    assert rows == [("OPEN", None), ("RESOLVED", "Ajay")]


def test_unchanged_state_adds_no_history(repo) -> None:
    repo.sync_events(IID, FLAG, [_event()], at=T0)
    repo.sync_events(IID, FLAG, [_event()], at=T0 + timedelta(days=1))
    count = repo._store.conn.execute("SELECT count(*) FROM data_quality_event_history").fetchone()
    assert count == (1,)


def test_existing_events_are_seeded_once_on_migrate() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    store.conn.execute(
        "INSERT INTO data_quality_events (event_id, instrument_id, trade_date, dataset, severity,"
        " blocks_signal, event_type, description, detected_at, resolved_at, status, resolved_by)"
        " VALUES ('dq-old', ?, DATE '2025-12-31', 'daily_prices', 'HIGH', TRUE,"
        " 'UNEXPLAINED_GAP', 'x', ?, ?, 'RESOLVED', 'SYSTEM')",
        [IID, T0, T0 + timedelta(days=10)],
    )
    store.migrate()
    store.migrate()  # idempotent
    rows = store.conn.execute(
        "SELECT status, valid_from, valid_to FROM data_quality_event_history ORDER BY valid_from"
    ).fetchall()
    assert rows == [
        ("OPEN", T0, T0 + timedelta(days=10)),
        ("RESOLVED", T0 + timedelta(days=10), None),
    ]
    repo = DuckDBDataQualityRepository(store)
    assert repo.blocked_instruments([IID], AS_OF, known_at=T0 + timedelta(days=5)) == {
        IID: ("UNEXPLAINED_GAP",)
    }
    assert repo.blocked_instruments([IID], AS_OF, known_at=T0 + timedelta(days=11)) == {}


def test_an_older_knowledge_time_replaces_the_current_interval(repo) -> None:
    repo.sync_events(IID, FLAG, [_event()], at=T0 + timedelta(days=10))
    repo.sync_events(IID, FLAG, [_event(blocks=False)], at=T0)  # re-run as of an earlier time
    rows = repo._store.conn.execute(
        "SELECT blocks_signal, valid_from, valid_to FROM data_quality_event_history"
    ).fetchall()
    assert rows == [(False, T0 + timedelta(days=10), None)]
