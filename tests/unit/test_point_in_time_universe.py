"""Point-in-time universe (audit P0-4).

Series and trade-to-trade status come from the NSE bhavcopy of each date, delisted names
take part while they traded, and the survivorship label is derived from the data with reasons.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import (
    SURVIVORSHIP_WINDOW_DAYS,
    UniverseBuilder,
    derive_survivorship,
)
from vcp_scanner.domain.enums import SurvivorshipStatus
from vcp_scanner.domain.universe import SurvivorshipEvidence

T0 = datetime(2024, 1, 1, tzinfo=UTC)  # when everything below was "known"
AS_OF = date(2023, 6, 30)


@pytest.fixture
def store():  # type: ignore[no-untyped-def]
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


def _config() -> UniverseConfig:
    return UniverseConfig(
        min_close_price=10.0,
        min_daily_turnover_inr=5_000_000.0,
        min_avg_traded_value_50d_inr=5_000_000.0,
    )


def _bars(store: DuckDBStore, iid: str, last: date, n: int = 300) -> None:
    store.conn.execute(
        """
        INSERT INTO daily_prices (instrument_id, trade_date, open_raw, high_raw, low_raw,
            close_raw, volume_raw, primary_provider, data_status, source_run_id, source_hash,
            known_from)
        SELECT ?, CAST(? AS DATE) - CAST(i AS INTEGER), 100, 100, 100, 100, 100000,
               'NSE_BHAVCOPY', 'OK', 'r', 'h', ?
        FROM range(0, ?) t(i)
        """,
        [iid, last, T0, n],
    )


def _series(store: DuckDBStore, iid: str, start: date, end: date, series: str) -> None:
    store.conn.execute(
        """
        INSERT INTO daily_series (trade_date, symbol, series, instrument_id, isin, recorded_at)
        SELECT CAST(? AS DATE) + CAST(i AS INTEGER), ?, ?, ?, 'INE000A01011', ?
        FROM range(0, ?) t(i)
        """,
        [start, iid, series, iid, T0, (end - start).days + 1],
    )


def _today_master(store: DuckDBStore, iid: str, series: str = "EQ") -> None:
    store.conn.execute(
        "INSERT INTO security_master_history (instrument_id, series, exchange, valid_from,"
        " known_from) VALUES (?, ?, 'NSE', '2000-01-01', ?)",
        [iid, series, T0],
    )


def _manifest(store: DuckDBStore, start: date, end: date) -> None:
    store.conn.execute(
        """
        INSERT INTO bhavcopy_files (trade_date, sha256, status, url, file_format, recorded_at)
        SELECT CAST(? AS DATE) + CAST(i AS INTEGER), 'x' || i, 'OK', 'u', 'CM_LEGACY', ?
        FROM range(0, ?) t(i)
        """,
        [start, T0, (end - start).days + 1],
    )


def _flags(store: DuckDBStore, flag: str, start: date) -> None:
    store.conn.execute(
        "INSERT INTO surveillance_flags_history (instrument_id, flag_type, valid_from, source,"
        " known_from) VALUES ('NSE_EQ|OTHER', ?, ?, 'NSE', ?)",
        [flag, start, T0],
    )


def _build(store: DuckDBStore, as_of: date = AS_OF):  # type: ignore[no-untyped-def]
    return UniverseBuilder(DuckDBUniverseRepository(store), _config()).build_snapshot(
        as_of, known_at=T0
    )


# --- series and T2T per date -----------------------------------------------------------


def test_series_and_t2t_come_from_that_days_bhavcopy(store: DuckDBStore) -> None:
    # Today EQUITY_L says EQ, but in mid-2023 the stock traded in BE (trade-to-trade).
    _bars(store, "NSE_EQ|MOVER", date(2023, 12, 29))
    _today_master(store, "NSE_EQ|MOVER", "EQ")
    _series(store, "NSE_EQ|MOVER", date(2022, 1, 1), date(2023, 3, 31), "EQ")
    _series(store, "NSE_EQ|MOVER", date(2023, 4, 1), date(2023, 9, 30), "BE")
    _series(store, "NSE_EQ|MOVER", date(2023, 10, 1), date(2023, 12, 29), "EQ")

    _, mid = _build(store, AS_OF)
    assert (mid[0].series, mid[0].t2t_flag, mid[0].eligible) == ("BE", "YES", False)
    _, end = _build(store, date(2023, 12, 29))
    assert (end[0].series, end[0].t2t_flag, end[0].eligible) == ("EQ", None, True)


def test_delisted_name_takes_part_while_it_traded(store: DuckDBStore) -> None:
    # No security-master row at all (delisted before today's EQUITY_L): exchange comes from
    # the bhavcopy, so it is screened like any other stock on dates it traded.
    _bars(store, "NSE_EQ|GONE", AS_OF)
    _series(store, "NSE_EQ|GONE", date(2022, 1, 1), AS_OF, "EQ")
    _, members = _build(store)
    assert [(m.instrument_id, m.series, m.eligible) for m in members] == [
        ("NSE_EQ|GONE", "EQ", True)
    ]


# --- survivorship label ----------------------------------------------------------------


def _evidence(**kw: object) -> SurvivorshipEvidence:
    base: dict[str, object] = {
        "window_start": AS_OF - timedelta(days=SURVIVORSHIP_WINDOW_DAYS),
        "missing_price_days": 0,
        "first_price_day": date(2021, 1, 1),
        "flag_history_start": {"ASM": date(2021, 1, 1), "GSM": date(2021, 1, 1)},
        "delistings": 100,
    }
    base.update(kw)
    return SurvivorshipEvidence(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("evidence", "status", "detail"),
    [
        (_evidence(), SurvivorshipStatus.POINT_IN_TIME_COMPLETE, None),
        (
            _evidence(flag_history_start={"ASM": date(2026, 10, 1), "GSM": None}),
            SurvivorshipStatus.PARTIAL,
            "ASM history starts 2026-10-01; GSM list never collected",
        ),
        (
            _evidence(missing_price_days=3),
            SurvivorshipStatus.PARTIAL,
            "bhavcopy history incomplete: 3 day(s) missing since 2022-06-15 "
            "(history starts 2021-01-01)",
        ),
        (
            _evidence(missing_price_days=None, delistings=0),
            SurvivorshipStatus.BIASED,
            "prices cover current listings only",
        ),
        (
            _evidence(missing_price_days=None),
            SurvivorshipStatus.PARTIAL,
            "no NSE bhavcopy history: delisted names lack prices",
        ),
    ],
)
def test_derive_survivorship(
    evidence: SurvivorshipEvidence, status: SurvivorshipStatus, detail: str | None
) -> None:
    assert derive_survivorship(evidence, AS_OF) == (status, detail)


def test_snapshot_label_from_stored_data(store: DuckDBStore) -> None:
    _bars(store, "NSE_EQ|A", AS_OF)
    _series(store, "NSE_EQ|A", date(2022, 1, 1), AS_OF, "EQ")
    _manifest(store, date(2021, 1, 1), AS_OF)

    # ASM/GSM collection started after the as-of date: PARTIAL, with the reason stored.
    _flags(store, "ASM", date(2026, 10, 1))
    _flags(store, "GSM", date(2026, 10, 1))
    snap, _ = _build(store)
    assert snap.survivorship_status is SurvivorshipStatus.PARTIAL
    assert (
        snap.survivorship_detail == "ASM history starts 2026-10-01; GSM history starts 2026-10-01"
    )
    DuckDBUniverseRepository(store).save_snapshot(snap, [])
    stored = store.conn.execute(
        "SELECT survivorship_status, survivorship_detail FROM universe_snapshots"
    ).fetchone()
    assert stored == ("PARTIAL", snap.survivorship_detail)

    # Lists known from before the as-of date and a full price window: complete.
    _flags(store, "ASM", date(2022, 1, 1))
    _flags(store, "GSM", date(2022, 1, 1))
    snap, _ = _build(store)
    assert (snap.survivorship_status, snap.survivorship_detail) == (
        SurvivorshipStatus.POINT_IN_TIME_COMPLETE,
        None,
    )


def test_missing_bhavcopy_days_in_the_window_make_it_partial(store: DuckDBStore) -> None:
    _bars(store, "NSE_EQ|A", AS_OF)
    _manifest(store, date(2022, 6, 1), AS_OF - timedelta(days=5))
    _flags(store, "ASM", date(2021, 1, 1))
    _flags(store, "GSM", date(2021, 1, 1))
    snap, _ = _build(store)
    assert snap.survivorship_status is SurvivorshipStatus.PARTIAL
    assert "bhavcopy history incomplete" in (snap.survivorship_detail or "")
