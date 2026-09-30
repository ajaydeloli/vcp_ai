"""Bhavcopy daily-file ingestion: precedence over Kite, idempotence, stop rules (step 2.3)."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from vcp_scanner.data.ingestion.bhavcopy_worker import BhavcopyIngestionWorker
from vcp_scanner.data.providers.nse_bhavcopy import (
    HOLIDAY_URL,
    NseBhavcopyProvider,
    bhavcopy_url,
)
from vcp_scanner.data.repositories.duckdb_bhavcopy_repository import DuckDBBhavcopyRepository
from vcp_scanner.data.repositories.duckdb_identity_repository import DuckDBIdentityRepository
from vcp_scanner.data.repositories.duckdb_instrument_repository import DuckDBInstrumentRepository
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.bhavcopy import BhavcopyFileStatus
from vcp_scanner.domain.enums import Timeframe
from vcp_scanner.domain.market import Candle, Instrument

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bhavcopy"
REAL_0728 = (FIXTURES / "cm28JUL2022bhav.csv.zip").read_bytes()
HEADER = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
    "TOTALTRADES,ISIN,\n"
)
TATA = "NSE_EQ|TATASTEEL"


def _legacy(d: date, rows: list[tuple[str, str, float, str]]) -> bytes:
    stamp = d.strftime("%d-%b-%Y").upper()
    text = HEADER + "".join(
        f"{sym},{ser},{c},{c},{c},{c},{c},{c},1000,{c * 1000},{stamp},10,{isin},\n"
        for sym, ser, c, isin in rows
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"cm{d.strftime('%d%b%Y').upper()}bhav.csv", text)
    return buf.getvalue()


FILES: dict[date, bytes] = {
    date(2022, 7, 28): REAL_0728,
    date(2022, 7, 29): _legacy(
        date(2022, 7, 29),
        [
            ("TATASTEEL", "EQ", 104.3, "INE081A01020"),
            ("TWOSER", "EQ", 50.0, "INE999Z01011"),
            ("TWOSER", "BE", 49.0, "INE999Z01011"),
        ],
    ),
    date(2022, 8, 1): _legacy(date(2022, 8, 1), [("TATASTEEL", "EQ", 106.0, "INE081A01020")]),
}


class _Session:
    def __init__(self, files: dict[date, bytes]) -> None:
        self.by_url = {bhavcopy_url(d): payload for d, payload in files.items()}
        self.calls: list[str] = []

    def get(self, url: str, timeout: float) -> Any:
        self.calls.append(url)
        if url == HOLIDAY_URL:
            response = MagicMock(status_code=200)
            response.json.return_value = {"CM": []}
            return response
        payload = self.by_url.get(url)
        return MagicMock(status_code=200 if payload is not None else 404, content=payload)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _setup(
    tmp_path: Path, files: dict[date, bytes] | None = None, now: datetime | None = None
) -> tuple[DuckDBStore, BhavcopyIngestionWorker, _Session, _Clock]:
    store = DuckDBStore(":memory:")
    store.migrate()
    clock = _Clock(now or datetime(2026, 9, 30, 12, tzinfo=UTC))
    DuckDBInstrumentRepository(store, clock=clock).save_instruments(
        [Instrument(TATA, "TATASTEEL", isin="INE081A01020")]
    )
    session = _Session(FILES if files is None else files)
    provider = NseBhavcopyProvider(tmp_path, session=session, sleep=lambda s: None)
    worker = BhavcopyIngestionWorker(
        provider,
        DuckDBMarketDataRepository(store, clock=clock),
        DuckDBBhavcopyRepository(store),
        DuckDBIdentityRepository(store),
        clock=clock,
    )
    return store, worker, session, clock


def _current(store: DuckDBStore, iid: str, d: date) -> tuple[Any, ...] | None:
    return store.conn.execute(
        "SELECT primary_provider, close_raw, selection_reason FROM daily_prices"
        " WHERE instrument_id = ? AND trade_date = ? AND known_to IS NULL",
        [iid, d],
    ).fetchone()


def _kite_bar(iid: str, d: date, close: float) -> Candle:
    return Candle(
        iid,
        datetime(d.year, d.month, d.day, tzinfo=UTC),
        Timeframe.DAILY,
        close,
        close,
        close,
        close,
        10,
        "KITE",
    )


def test_days_are_ingested_in_order_with_weekend_as_no_session(tmp_path: Path) -> None:
    store, worker, _, _ = _setup(tmp_path)
    s = worker.run(date(2022, 7, 28), date(2022, 8, 1))
    assert (s.days_ok, s.days_no_session, s.stopped_at, s.status) == (3, 2, None, "SUCCESS")
    assert _current(store, TATA, date(2022, 7, 28)) == ("NSE_BHAVCOPY", 100.35, "series=EQ")
    assert _current(store, TATA, date(2022, 7, 29)) == ("NSE_BHAVCOPY", 104.3, "series=EQ")
    assert _current(store, TATA, date(2022, 8, 1))[1] == 106.0  # type: ignore[index]
    # Two series for one stock on one day: one bar, from EQ.
    assert _current(store, "NSE_EQ|TWOSER", date(2022, 7, 29)) == (
        "NSE_BHAVCOPY",
        50.0,
        "series=EQ",
    )
    manifest = DuckDBBhavcopyRepository(store).latest_files(date(2022, 7, 28), date(2022, 8, 1))
    assert [m.status for m in manifest.values()] == [
        BhavcopyFileStatus.OK,
        BhavcopyFileStatus.OK,
        BhavcopyFileStatus.NO_SESSION,
        BhavcopyFileStatus.NO_SESSION,
        BhavcopyFileStatus.OK,
    ]
    run = store.conn.execute(
        "SELECT provider, status, records_written FROM ingestion_runs"
    ).fetchone()
    assert run is not None and run[:2] == ("NSE_BHAVCOPY", "SUCCESS") and run[2] > 0


def test_bhavcopy_supersedes_kite_and_kite_never_supersedes_bhavcopy(tmp_path: Path) -> None:
    store, worker, _, _ = _setup(tmp_path)
    market = DuckDBMarketDataRepository(store)
    assert market.save_daily([_kite_bar(TATA, date(2022, 7, 28), 94.55)]) == 1

    s = worker.run(date(2022, 7, 28), date(2022, 7, 28))
    assert s.superseded == {"KITE": 1}
    assert _current(store, TATA, date(2022, 7, 28))[:2] == ("NSE_BHAVCOPY", 100.35)  # type: ignore[index]
    closed = store.conn.execute(
        "SELECT known_to IS NOT NULL FROM daily_prices WHERE primary_provider = 'KITE'"
    ).fetchall()
    assert closed == [(True,)]

    # A later Kite fetch for the same session is ignored.
    assert market.save_daily([_kite_bar(TATA, date(2022, 7, 28), 94.60)]) == 0
    assert _current(store, TATA, date(2022, 7, 28))[:2] == ("NSE_BHAVCOPY", 100.35)  # type: ignore[index]


def test_rerun_is_a_no_op_and_refresh_rewrites_nothing(tmp_path: Path) -> None:
    store, worker, session, _ = _setup(tmp_path)
    worker.run(date(2022, 7, 28), date(2022, 8, 1))
    rows = store.conn.execute("SELECT count(*) FROM daily_prices").fetchone()
    calls = len(session.calls)

    again = worker.run(date(2022, 7, 28), date(2022, 8, 1))
    assert (again.days_skipped, again.days_ok, len(session.calls)) == (5, 0, calls)

    refreshed = worker.run(date(2022, 7, 28), date(2022, 8, 1), refresh=True)
    assert refreshed.days_ok == 3 and refreshed.bars_written == 0
    assert refreshed.bars_unchanged > 0
    assert store.conn.execute("SELECT count(*) FROM daily_prices").fetchone() == rows


def test_a_recent_missing_day_stops_the_run_before_later_days(tmp_path: Path) -> None:
    # Seen on 2022-08-02 (IST): 2022-08-01 is not published yet, 2022-08-02 is.
    files = {
        date(2022, 7, 29): FILES[date(2022, 7, 29)],
        date(2022, 8, 2): _legacy(date(2022, 8, 2), [("TATASTEEL", "EQ", 107.0, "INE081A01020")]),
    }
    now = datetime(2022, 8, 2, 14, tzinfo=UTC)
    store, worker, _, _ = _setup(tmp_path, files, now)
    s = worker.run(date(2022, 7, 29), date(2022, 8, 2))
    assert s.stopped_at == date(2022, 8, 1)
    assert s.stop_status is BhavcopyFileStatus.PENDING
    assert s.status == "SUCCESS"  # waiting for NSE is not a failure
    assert _current(store, TATA, date(2022, 8, 2)) is None


def test_a_bad_file_is_recorded_as_error_and_stops(tmp_path: Path) -> None:
    files = {date(2022, 7, 29): b"<html>blocked</html>"}
    store, worker, _, _ = _setup(tmp_path, files)
    s = worker.run(date(2022, 7, 29), date(2022, 8, 1))
    assert (s.stopped_at, s.stop_status, s.status) == (
        date(2022, 7, 29),
        BhavcopyFileStatus.ERROR,
        "FAILED",
    )
    record = DuckDBBhavcopyRepository(store).latest_file(date(2022, 7, 29))
    assert record is not None and record.status is BhavcopyFileStatus.ERROR
    assert "not a zip" in (record.detail or "")


def test_future_days_are_not_requested(tmp_path: Path) -> None:
    now = datetime(2022, 7, 29, 6, tzinfo=UTC)
    _, worker, session, _ = _setup(tmp_path, FILES, now)
    s = worker.run(date(2022, 7, 28), date(2022, 8, 5))
    assert s.days_ok == 2
    assert not any("AUG2022" in url for url in session.calls)
