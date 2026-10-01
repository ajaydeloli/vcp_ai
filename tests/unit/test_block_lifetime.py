"""Audit P1-2b: a dated data-quality block ends once the bad bar has left every lookback.

Synthetic bars and events (AGENTS.md rule 10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from vcp_scanner.config.models import DataConfig, QualityGateConfig, ScannerConfig
from vcp_scanner.data.repositories.duckdb_market_repository import (
    DuckDBMarketDataRepository,
    FinalDailyBar,
)
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity, make_event_id

IID = "NSE_EQ|LIFE"
AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
GAP = DataQualityFlag.UNEXPLAINED_GAP
EVENT_DAY = date(2024, 1, 3)
# Ten bars: 2024-01-01 .. 2024-01-12 on weekdays; the event bar is the third.
DAYS = [date(2024, 1, d) for d in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12)]


@pytest.fixture()
def store():
    s = DuckDBStore(":memory:")
    s.migrate()
    DuckDBMarketDataRepository(s).save_final_daily(
        [FinalDailyBar(IID, d, 10.0, 10.0, 10.0, 10.0, 100, "EQ") for d in DAYS],
        known_from=AT,
        source_run_id="t",
    )
    yield s
    s.close()


def _event(trade_date: date | None = EVENT_DAY, detected_at: datetime = AT) -> DataQualityEvent:
    return DataQualityEvent(
        event_id=make_event_id(GAP, IID, str(trade_date)), instrument_id=IID, flag=GAP,
        severity=EventSeverity.HIGH, detected_at=detected_at, description="gap",
        trade_date=trade_date, blocks_signal=True,
    )  # fmt: skip


def test_block_ends_after_lifetime_bars_from_the_event_bar(store: DuckDBStore) -> None:
    gate = DuckDBDataQualityRepository(store, block_lifetime_bars=3)
    gate.sync_events(IID, GAP, [_event()], at=AT)
    # Bars in [event, as-of]: 01-03 -> 1, 01-04 -> 2, 01-05 -> 3 (window of 3 starts at the
    # event bar, so it no longer spans the jump from the previous bar).
    assert gate.blocked_instruments([IID], date(2024, 1, 2)) == {}  # before the event
    assert IID in gate.blocked_instruments([IID], date(2024, 1, 3))
    assert IID in gate.blocked_instruments([IID], date(2024, 1, 4))
    assert gate.blocked_instruments([IID], date(2024, 1, 5)) == {}
    assert gate.blocked_instruments([IID], date(2024, 1, 12)) == {}
    # A backtest at an earlier as-of date still sees the block: no look-ahead either way.
    assert IID in gate.blocked_instruments([IID], date(2024, 1, 4))


def test_calendar_days_without_bars_do_not_count(store: DuckDBStore) -> None:
    gate = DuckDBDataQualityRepository(store, block_lifetime_bars=4)
    gate.sync_events(IID, GAP, [_event()], at=AT)
    # 01-05 is the 3rd bar, the weekend adds none; the 4th bar is 01-08.
    assert IID in gate.blocked_instruments([IID], date(2024, 1, 7))
    assert gate.blocked_instruments([IID], date(2024, 1, 8)) == {}


def test_without_a_lifetime_blocks_never_end(store: DuckDBStore) -> None:
    gate = DuckDBDataQualityRepository(store)
    gate.sync_events(IID, GAP, [_event()], at=AT)
    assert IID in gate.blocked_instruments([IID], date(2030, 1, 1))


def test_undated_event_never_expires(store: DuckDBStore) -> None:
    gate = DuckDBDataQualityRepository(store, block_lifetime_bars=1)
    gate.sync_events(IID, GAP, [_event(trade_date=None)], at=AT)
    assert IID in gate.blocked_instruments([IID], date(2030, 1, 1))


def test_point_in_time_counts_only_bars_known_then(store: DuckDBStore) -> None:
    gate = DuckDBDataQualityRepository(store, block_lifetime_bars=3)
    early = AT - timedelta(days=1)
    gate.sync_events(IID, GAP, [_event(detected_at=early)], at=early)
    as_of = date(2024, 1, 12)
    # Bars were first known at AT: a run as known just before that sees no bars after the
    # event, so the block still applies; as known at AT, the block has ended.
    assert IID in gate.blocked_instruments([IID], as_of, known_at=AT - timedelta(hours=1))
    assert gate.blocked_instruments([IID], as_of, known_at=AT) == {}


def test_config_default_and_validation() -> None:
    cfg = ScannerConfig()
    assert cfg.data.quality.block_lifetime_bars == 253 == cfg.longest_lookback_bars()
    assert ScannerConfig(data=DataConfig(quality=QualityGateConfig(block_lifetime_bars=None)))
    with pytest.raises(ValidationError, match="longest lookback"):
        ScannerConfig(data=DataConfig(quality=QualityGateConfig(block_lifetime_bars=200)))
