"""Audit P1-2c: a long trading absence restarts a stock's price history.

GOODYEAR's prices are real (last NSE bar 2023-10-25, back 2026-04-20); the session calendar
and the other bars are synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.config.models import UnexplainedGapConfig
from vcp_scanner.data.quality.absence import missed_sessions, trading_absence_events
from vcp_scanner.data.quality.scanner import QualityScanner
from vcp_scanner.data.reconciliation.gap_detector import GapDetector
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import (
    DuckDBMarketDataRepository,
    FinalDailyBar,
)
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.market import Candle

AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
IID = "NSE_EQ|GOODYEAR"


def _weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _candle(d: date, o: float, c: float) -> Candle:
    return Candle(
        instrument_id=IID, timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC), timeframe="D",
        open=o, high=max(o, c), low=min(o, c), close=c, volume=100, provider="NSE_BHAVCOPY",
    )  # fmt: skip


def test_missed_sessions_counts_only_sessions_strictly_between() -> None:
    s = _weekdays(date(2024, 1, 1), date(2024, 1, 31))
    assert missed_sessions(s, date(2024, 1, 1), date(2024, 1, 2)) == 0
    assert missed_sessions(s, date(2024, 1, 5), date(2024, 1, 8)) == 0  # weekend
    assert missed_sessions(s, date(2024, 1, 1), date(2024, 1, 31)) == 21


@pytest.mark.parametrize(("missed", "expected"), [(19, 0), (20, 1)])
def test_threshold(missed: int, expected: int) -> None:
    sessions = _weekdays(date(2024, 1, 1), date(2024, 3, 29))
    a, b = sessions[0], sessions[missed + 1]
    events = trading_absence_events(
        IID, [_candle(a, 10, 10), _candle(b, 10, 10)], sessions,
        min_missed_sessions=20, detected_at=AT,
    )  # fmt: skip
    assert len(events) == expected
    if events:
        (e,) = events
        assert (e.flag, e.trade_date, e.blocks_signal) == (DataQualityFlag.TRADING_ABSENCE, b, True)
        assert e.context is not None and e.context["missed_sessions"] == 20


def test_holidays_are_not_missed_sessions() -> None:
    # Ten weekdays with no session (an exchange closure) are not an absence.
    sessions = [d for d in _weekdays(date(2024, 1, 1), date(2024, 3, 29))
                if not date(2024, 2, 1) <= d <= date(2024, 2, 14)]  # fmt: skip
    candles = [_candle(date(2024, 1, 31), 10, 10), _candle(date(2024, 2, 15), 10, 10)]
    assert trading_absence_events(IID, candles, sessions, min_missed_sessions=5,
                                  detected_at=AT) == []  # fmt: skip


# --- end to end: scanner + gate -------------------------------------------------------------

SESSIONS = _weekdays(date(2023, 9, 1), date(2026, 5, 29))
BEFORE = [d for d in SESSIONS if d <= date(2023, 10, 25)]
AFTER = [d for d in SESSIONS if d >= date(2026, 4, 20)]


@pytest.fixture()
def store():
    s = DuckDBStore(":memory:")
    s.migrate()
    bars = [FinalDailyBar(IID, d, 1273.7, 1273.7, 1273.7, 1273.7, 100, "EQ") for d in BEFORE]
    bars += [FinalDailyBar(IID, d, 800.0, 800.0, 797.45, 797.45, 100, "EQ") for d in AFTER]
    DuckDBMarketDataRepository(s).save_final_daily(bars, known_from=AT, source_run_id="t")
    yield s
    s.close()


def _scanner(store: DuckDBStore, sessions: list[date] | None) -> QualityScanner:
    return QualityScanner(
        DuckDBMarketDataRepository(store), DuckDBCorporateActionRepository(store),
        DuckDBDataQualityRepository(store), GapDetector(UnexplainedGapConfig()),
        sessions=sessions, absence_min_missed_sessions=20,
    )  # fmt: skip


def test_return_after_absence_blocks_until_a_full_lookback_and_gap_only_warns(
    store: DuckDBStore,
) -> None:
    summary = _scanner(store, SESSIONS).scan([IID], detected_at=AT)
    assert summary.absence_events == 1 and summary.blocking == 1

    events = {e.flag: e for e in DuckDBDataQualityRepository(store).load_events()}
    absence, gap = events[DataQualityFlag.TRADING_ABSENCE], events[DataQualityFlag.UNEXPLAINED_GAP]
    assert absence.trade_date == date(2026, 4, 20) and absence.blocks_signal
    assert absence.context is not None and absence.context["missed_sessions"] > 600
    assert gap.blocks_signal is False  # -37 % looks split-like, but it is a return
    assert gap.context is not None and gap.context["after_trading_absence_sessions"] > 600

    gate = DuckDBDataQualityRepository(store, block_lifetime_bars=10)
    assert gate.blocked_instruments([IID], date(2023, 10, 25)) == {}  # before the absence
    assert gate.blocked_instruments([IID], AFTER[8]) == {IID: ("TRADING_ABSENCE",)}
    assert gate.blocked_instruments([IID], AFTER[9]) == {}  # 10 bars since the return


def test_without_a_session_calendar_nothing_changes(store: DuckDBStore) -> None:
    summary = _scanner(store, None).scan([IID], detected_at=AT)
    assert summary.absence_events == 0
    (gap,) = DuckDBDataQualityRepository(store).load_events()
    assert gap.flag is DataQualityFlag.UNEXPLAINED_GAP and gap.blocks_signal is True


def test_market_sessions_come_from_settled_bhavcopy_days(store: DuckDBStore) -> None:
    repo = DuckDBMarketDataRepository(store)
    assert repo.load_market_sessions() is None
    rows = [
        (date(2024, 1, 2), "a", "OK"), (date(2024, 1, 2), "b", "OK"),  # re-downloaded file
        (date(2024, 1, 1), "c", "OK"), (date(2024, 1, 6), "d", "NO_SESSION"),
    ]  # fmt: skip
    for d, sha, status in rows:
        store.conn.execute(
            "INSERT INTO bhavcopy_files (trade_date, sha256, status, url, file_format,"
            " recorded_at) VALUES (?, ?, ?, 'u', 'csv', ?)",
            [d, sha, status, AT],
        )
    assert repo.load_market_sessions() == [date(2024, 1, 1), date(2024, 1, 2)]
