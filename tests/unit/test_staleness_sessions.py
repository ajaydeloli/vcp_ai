"""Audit P2-2: one staleness rule in NSE sessions, tiered by use (DATA_SPECIFICATION 26)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.config.models import DataConfig, StalenessConfig
from vcp_scanner.data.repositories.session_calendar import load_session_calendar
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.sessions import missed_sessions_since

T0 = datetime(2024, 7, 1, 12, tzinfo=UTC)


@pytest.fixture
def store() -> DuckDBStore:
    s = DuckDBStore(":memory:")
    s.migrate()
    return s


def _bhavcopy(store: DuckDBStore, day: date, recorded_at: datetime, status: str = "OK") -> None:
    store.conn.execute(
        "INSERT INTO bhavcopy_files (trade_date, sha256, status, url, file_format, recorded_at)"
        " VALUES (?, ?, ?, 'u', 'csv', ?)",
        [day, f"h{day}{status}", status, recorded_at],
    )


def _bar(store: DuckDBStore, iid: str, day: date, known_from: datetime) -> None:
    store.conn.execute(
        """
        INSERT INTO daily_prices (
            instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw, volume_raw,
            primary_provider, data_status, source_run_id, source_hash, known_from
        ) VALUES (?, ?, 10, 10, 10, 10, 100, 'MOCK', 'OK', 'run', 'hash', ?)
        """,
        [iid, day, known_from],
    )


def test_missed_sessions_since_counts_sessions_after_the_last_bar() -> None:
    sessions = [date(2024, 6, d) for d in (24, 25, 27, 28)]  # 26th a holiday
    assert missed_sessions_since(sessions, date(2024, 6, 28), date(2024, 6, 28)) == 0
    assert missed_sessions_since(sessions, date(2024, 6, 25), date(2024, 6, 28)) == 2
    assert missed_sessions_since(sessions, date(2024, 6, 28), date(2024, 6, 30)) == 0  # weekend
    assert missed_sessions_since([], date(2020, 1, 1), date(2024, 6, 28)) == 0


def test_calendar_is_settled_bhavcopy_days_known_at_the_cutoff(store: DuckDBStore) -> None:
    _bhavcopy(store, date(2024, 6, 27), T0)
    _bhavcopy(store, date(2024, 6, 28), T0 + timedelta(days=1))  # recorded later
    _bhavcopy(store, date(2024, 6, 29), T0, status="NO_SESSION")
    _bhavcopy(store, date(2024, 7, 1), T0)  # after the as-of date
    as_of = date(2024, 6, 30)
    assert load_session_calendar(store.conn, as_of) == [date(2024, 6, 27), date(2024, 6, 28)]
    assert load_session_calendar(store.conn, as_of, T0) == [date(2024, 6, 27)]


def test_calendar_falls_back_to_bar_dates_without_bhavcopy(store: DuckDBStore) -> None:
    _bar(store, "A", date(2024, 6, 27), T0)
    _bar(store, "B", date(2024, 6, 28), T0 + timedelta(days=1))
    as_of = date(2024, 6, 28)
    assert load_session_calendar(store.conn, as_of) == [date(2024, 6, 27), date(2024, 6, 28)]
    assert load_session_calendar(store.conn, as_of, T0) == [date(2024, 6, 27)]


def test_staleness_lives_in_data_quality_with_tiered_defaults() -> None:
    cfg = DataConfig().quality.staleness
    assert cfg == StalenessConfig(universe_max_missed_sessions=5, rs_max_missed_sessions=1)


def test_old_calendar_day_keys_are_refused() -> None:
    from pydantic import ValidationError

    from vcp_scanner.config.models import RSConfig, UniverseConfig

    with pytest.raises(ValidationError):
        UniverseConfig(max_staleness_days=30)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        RSConfig(max_staleness_days=4)  # type: ignore[call-arg]


def test_rs_calendar_is_as_known_when_the_universe_was_built(store: DuckDBStore) -> None:
    from vcp_scanner.data.repositories.duckdb_rs_repository import (
        DuckDBRelativeStrengthRepository,
    )

    _bhavcopy(store, date(2024, 6, 27), T0)
    _bhavcopy(store, date(2024, 6, 28), T0 + timedelta(days=1))
    store.conn.execute(
        "INSERT INTO universe_snapshots (universe_snapshot_id, universe_name, as_of_date,"
        " created_at, config_hash, method_version, survivorship_status)"
        " VALUES ('uv', 'VCP_BASE', DATE '2024-06-28', ?, 'h', '2.0', 'PARTIAL')",
        [T0],
    )
    repo = DuckDBRelativeStrengthRepository(store)
    assert repo.load_sessions(date(2024, 6, 28), "uv") == [date(2024, 6, 27)]
    assert repo.load_sessions(date(2024, 6, 28), "unknown") == [
        date(2024, 6, 27),
        date(2024, 6, 28),
    ]
