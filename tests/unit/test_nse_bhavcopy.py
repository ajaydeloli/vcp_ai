"""NSE bhavcopy provider, parser and manifest (audit step 2.1).

The two zips in tests/fixtures/bhavcopy are real NSE files trimmed to a few rows (header and
row text unchanged): the legacy layout on TATASTEEL's split ex-date 2022-07-28, and the UDiFF
layout for 2026-09-29, whose closes and volumes matched Kite exactly in the 2.0 spike.
"""

from __future__ import annotations

import hashlib
import io
import math
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from vcp_scanner.data.providers.nse_bhavcopy import (
    NseBhavcopyProvider,
    bhavcopy_format,
    bhavcopy_url,
    classify_missing,
    parse_bhavcopy,
)
from vcp_scanner.data.repositories.duckdb_bhavcopy_repository import DuckDBBhavcopyRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.bhavcopy import (
    BhavcopyFileRecord,
    BhavcopyFileStatus,
    BhavcopyFormat,
)
from vcp_scanner.domain.errors import ProviderError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bhavcopy"
LEGACY_DAY = date(2022, 7, 28)
UDIFF_DAY = date(2026, 9, 29)
LEGACY_ZIP = (FIXTURES / "cm28JUL2022bhav.csv.zip").read_bytes()
UDIFF_ZIP = (FIXTURES / "BhavCopy_NSE_CM_0_0_0_20260929_F_0000.csv.zip").read_bytes()

LEGACY_HEADER = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
    "TOTALTRADES,ISIN,\n"
)


def _zip(text: str, name: str = "cm28JUL2022bhav.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def _legacy_row(symbol: str, o: str, h: str, lo: str, c: str, vol: str = "100") -> str:
    return f"{symbol},EQ,{o},{h},{lo},{c},{c},{c},{vol},1000,28-JUL-2022,10,INE000000001,\n"


# --- URLs and layouts ------------------------------------------------------------------


def test_layout_switches_on_2024_07_08() -> None:
    assert bhavcopy_format(date(2024, 7, 5)) is BhavcopyFormat.LEGACY
    assert bhavcopy_format(date(2024, 7, 8)) is BhavcopyFormat.UDIFF
    assert bhavcopy_url(date(2024, 7, 5)) == (
        "https://nsearchives.nseindia.com/content/historical/EQUITIES/2024/JUL/cm05JUL2024bhav.csv.zip"
    )
    assert bhavcopy_url(date(2024, 7, 8)) == (
        "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
    )


# --- Parsing real files ----------------------------------------------------------------


def test_legacy_file_parses_unadjusted_bars() -> None:
    parsed = parse_bhavcopy(LEGACY_DAY, LEGACY_ZIP)
    assert parsed.file_format is BhavcopyFormat.LEGACY
    by_symbol = {r.symbol: r for r in parsed.rows if r.series == "EQ"}
    tata = by_symbol["TATASTEEL"]
    # Split ex-date 10:1: raw prices, and NSE's PREVCLOSE is the unadjusted 959.40.
    assert (tata.open, tata.high, tata.low, tata.close) == (98.1, 102.0, 97.15, 100.35)
    assert tata.prev_close == 959.4
    assert tata.volume == 137156107
    assert tata.isin == "INE081A01012"
    assert {r.series for r in parsed.rows} == {"EQ", "SM"}
    assert sum(parsed.skipped_series.values()) == 2  # government bond rows
    assert parsed.rejected == []


def test_udiff_file_parses_and_matches_kite_close() -> None:
    parsed = parse_bhavcopy(UDIFF_DAY, UDIFF_ZIP)
    assert parsed.file_format is BhavcopyFormat.UDIFF
    by_symbol = {r.symbol: r for r in parsed.rows if r.series == "EQ"}
    assert (by_symbol["TITAN"].close, by_symbol["TITAN"].volume) == (4675.0, 1188414)
    assert (by_symbol["TATASTEEL"].close, by_symbol["TATASTEEL"].volume) == (188.0, 28159048)
    assert all(r.trade_date == UDIFF_DAY for r in parsed.rows)
    assert set(parsed.skipped_series) <= {"GB", "GS"}


def test_series_filter_is_configurable() -> None:
    parsed = parse_bhavcopy(LEGACY_DAY, LEGACY_ZIP, series=frozenset({"EQ"}))
    assert {r.series for r in parsed.rows} == {"EQ"}
    assert parsed.skipped_series.get("SM") == 2


# --- Whole-file errors -----------------------------------------------------------------


def test_file_for_another_session_is_refused() -> None:
    with pytest.raises(ProviderError, match="stamped 2022-07-28"):
        parse_bhavcopy(date(2022, 7, 29), LEGACY_ZIP)


def test_layout_without_expected_columns_is_refused() -> None:
    with pytest.raises(ProviderError, match="lacks"):
        parse_bhavcopy(LEGACY_DAY, _zip("SYMBOL,SERIES,CLOSE\nABC,EQ,1\n"))


def test_non_zip_payload_is_refused() -> None:
    with pytest.raises(ProviderError, match="not a zip"):
        parse_bhavcopy(LEGACY_DAY, b"<html>blocked</html>")


# --- Row-level rejects are recorded, never silently dropped ----------------------------


def test_bad_rows_are_rejected_with_reasons() -> None:
    text = (
        LEGACY_HEADER
        + _legacy_row("GOOD", "10", "11", "9", "10.5")
        + _legacy_row("GOOD", "10", "11", "9", "10.5")  # duplicate
        + _legacy_row("INVERTED", "10", "9", "11", "10")  # high < low
        + _legacy_row("NANCLOSE", "10", "11", "9", "nan")
        + _legacy_row("BLANK", "10", "11", "9", "")
        + _legacy_row("ZERO", "0", "0", "0", "0")
        + _legacy_row("FRACVOL", "10", "11", "9", "10", vol="12.5")
    )
    parsed = parse_bhavcopy(LEGACY_DAY, _zip(text))
    assert [r.symbol for r in parsed.rows] == ["GOOD"]
    reasons = {r.symbol: r.reason for r in parsed.rejected}
    assert reasons["GOOD"] == "duplicate symbol and series"
    assert "low > high" in reasons["INVERTED"]
    assert "not finite" in reasons["NANCLOSE"]
    assert "CLOSE missing" in reasons["BLANK"]
    assert "<= 0" in reasons["ZERO"]
    assert "not a whole number" in reasons["FRACVOL"]
    assert all(math.isfinite(r.close) for r in parsed.rows)


# --- What a 404 means ------------------------------------------------------------------


def test_classify_missing() -> None:
    today = date(2026, 9, 30)
    assert classify_missing(date(2026, 9, 30), today) is BhavcopyFileStatus.PENDING
    assert classify_missing(date(2026, 9, 28), today) is BhavcopyFileStatus.PENDING
    assert classify_missing(date(2026, 9, 27), today) is BhavcopyFileStatus.NO_SESSION
    holiday = date(2026, 9, 29)
    assert classify_missing(holiday, today, {holiday}) is BhavcopyFileStatus.NO_SESSION


# --- Downloads, cache and rate limit ---------------------------------------------------


def _response(status: int, content: bytes = b"") -> MagicMock:
    return MagicMock(status_code=status, content=content)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 6))
        self.now += seconds


def _provider(
    tmp_path: Path, session: MagicMock, clock: _Clock | None = None
) -> NseBhavcopyProvider:
    clock = clock or _Clock()
    return NseBhavcopyProvider(
        tmp_path, session=session, sleep=clock.sleep, monotonic=clock.monotonic
    )


def test_download_is_cached_unchanged_with_hash(tmp_path: Path) -> None:
    session = MagicMock()
    session.get.return_value = _response(200, UDIFF_ZIP)
    provider = _provider(tmp_path, session)

    first = provider.fetch_day(UDIFF_DAY)
    assert first.status == "OK" and not first.from_cache
    assert first.sha256 == hashlib.sha256(UDIFF_ZIP).hexdigest()
    assert first.cache_path == tmp_path / "2026" / "BhavCopy_NSE_CM_0_0_0_20260929_F_0000.csv.zip"
    assert first.cache_path.read_bytes() == UDIFF_ZIP

    second = provider.fetch_day(UDIFF_DAY)
    assert second.from_cache and second.sha256 == first.sha256
    assert session.get.call_count == 1

    provider.fetch_day(UDIFF_DAY, refresh=True)
    assert session.get.call_count == 2


def test_not_found_is_reported_and_nothing_cached(tmp_path: Path) -> None:
    session = MagicMock()
    session.get.return_value = _response(404)
    result = _provider(tmp_path, session).fetch_day(date(2025, 8, 15))
    assert result.status == "NOT_FOUND" and result.payload is None
    assert not any(tmp_path.rglob("*.zip"))


@pytest.mark.parametrize("failure", [_response(500), requests.ConnectionError("down")])
def test_other_failures_raise(tmp_path: Path, failure: object) -> None:
    session = MagicMock()
    if isinstance(failure, Exception):
        session.get.side_effect = failure
    else:
        session.get.return_value = failure
    with pytest.raises(ProviderError):
        _provider(tmp_path, session).fetch_day(UDIFF_DAY)


def test_requests_are_spaced_by_min_interval(tmp_path: Path) -> None:
    session = MagicMock()
    session.get.return_value = _response(404)
    clock = _Clock()
    provider = _provider(tmp_path, session, clock)
    provider.fetch_day(date(2025, 8, 15))
    clock.now += 0.25
    provider.fetch_day(date(2025, 8, 16))
    assert clock.slept == [0.75]


def test_holiday_list_parses(tmp_path: Path) -> None:
    session = MagicMock()
    response = _response(200)
    response.json.return_value = {
        "CM": [{"tradingDate": "15-Jan-2026"}, {"tradingDate": "26-Jan-2026"}]
    }
    session.get.return_value = response
    assert _provider(tmp_path, session).get_trading_holidays() == {
        date(2026, 1, 15),
        date(2026, 1, 26),
    }
    response.json.return_value = {"CM": [{"wrong": 1}]}
    with pytest.raises(ProviderError):
        _provider(tmp_path, session).get_trading_holidays()


# --- Manifest --------------------------------------------------------------------------


def test_manifest_updates_missing_dates_in_place_and_keeps_file_versions() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBBhavcopyRepository(store)
    day = date(2026, 9, 29)
    url, fmt = bhavcopy_url(day), BhavcopyFormat.UDIFF
    t = [datetime(2026, 9, 29, h, tzinfo=UTC) for h in (12, 13, 14, 15)]

    pending = BhavcopyFileRecord(day, BhavcopyFileStatus.PENDING, url, fmt)
    repo.record_file(pending, recorded_at=t[0])
    pending = BhavcopyFileRecord(day, BhavcopyFileStatus.PENDING, url, fmt)
    repo.record_file(pending, recorded_at=t[1])
    count = store.conn.execute("SELECT count(*) FROM bhavcopy_files").fetchone()
    assert count == (1,)

    ok = BhavcopyFileRecord(day, BhavcopyFileStatus.OK, url, fmt, sha256="a" * 64, row_count=3)
    repo.record_file(ok, recorded_at=t[2])
    assert repo.latest_file(day) == ok

    republished = BhavcopyFileRecord(day, BhavcopyFileStatus.OK, url, fmt, sha256="b" * 64)
    repo.record_file(republished, recorded_at=t[3])
    assert repo.latest_file(day) == republished
    count = store.conn.execute("SELECT count(*) FROM bhavcopy_files").fetchone()
    assert count == (3,)
    assert repo.latest_files(date(2026, 9, 1), date(2026, 9, 28)) == {}


def test_etf_units_in_equity_series_are_skipped() -> None:
    text = (
        LEGACY_HEADER
        + _legacy_row("STOCK", "10", "11", "9", "10.5")
        + "NIFTYBEES,EQ,10,11,9,10,10,10,100,1000,28-JUL-2022,10,INF204KB14I2,\n"
    )
    parsed = parse_bhavcopy(LEGACY_DAY, _zip(text))
    assert [r.symbol for r in parsed.rows] == ["STOCK"]
    assert parsed.skipped_series == {"EQ:INF": 1}
