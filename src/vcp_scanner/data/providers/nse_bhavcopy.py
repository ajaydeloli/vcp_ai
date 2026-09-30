"""NSE capital-market bhavcopy provider: raw daily prices (audit step 2; DATA_SPECIFICATION §21.2).

One file per trading session covers the whole market, with unadjusted OHLC, series and ISIN.

Layouts (checked 2026-09-30, step 2.0 spike):

* legacy, sessions up to 2024-07-05:
  ``content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip``
* UDiFF, sessions from 2024-07-08:
  ``content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip``

Facts the ingest relies on:

* A missing session (holiday, weekend) and a not-yet-published file both answer HTTP 404, so
  the provider reports ``NOT_FOUND`` and :func:`classify_missing` decides what that means.
* Sessions also happen on weekends (Muhurat Sunday 2023-11-12, special Saturday 2024-01-20),
  so callers probe every calendar day.
* ``PREVCLOSE`` is *not* adjusted on corporate-action ex-dates; it is stored for reference only.

Every downloaded zip is cached unchanged, with its sha256, so a day can be re-parsed without
re-downloading and the manifest proves which bytes were ingested.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import math
import os
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.data.schema import validate_ohlc
from vcp_scanner.domain.bhavcopy import (
    EQUITY_ISIN_PREFIX,
    EQUITY_SERIES,
    BhavcopyFileStatus,
    BhavcopyFormat,
    BhavcopyRow,
    ParsedBhavcopy,
    RejectedBhavcopyRow,
)
from vcp_scanner.domain.errors import ProviderError

logger = logging.getLogger(__name__)

UDIFF_FROM = date(2024, 7, 8)  # first session published in the UDiFF layout
ARCHIVE_BASE = "https://nsearchives.nseindia.com"
HOLIDAY_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
# A 404 for a date at least this many days old is final: NSE's archive is complete by then.
SETTLE_DAYS = 3

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Canonical field -> column name, per layout.
_COLUMNS: dict[BhavcopyFormat, dict[str, str]] = {
    BhavcopyFormat.LEGACY: {
        "date": "TIMESTAMP",
        "symbol": "SYMBOL",
        "series": "SERIES",
        "isin": "ISIN",
        "open": "OPEN",
        "high": "HIGH",
        "low": "LOW",
        "close": "CLOSE",
        "last": "LAST",
        "prev_close": "PREVCLOSE",
        "volume": "TOTTRDQTY",
        "turnover": "TOTTRDVAL",
        "trades": "TOTALTRADES",
    },
    BhavcopyFormat.UDIFF: {
        "date": "TradDt",
        "symbol": "TckrSymb",
        "series": "SctySrs",
        "isin": "ISIN",
        "open": "OpnPric",
        "high": "HghPric",
        "low": "LwPric",
        "close": "ClsPric",
        "last": "LastPric",
        "prev_close": "PrvsClsgPric",
        "volume": "TtlTradgVol",
        "turnover": "TtlTrfVal",
        "trades": "TtlNbOfTxsExctd",
    },
}
_DATE_FORMATS: dict[BhavcopyFormat, str] = {
    BhavcopyFormat.LEGACY: "%d-%b-%Y",
    BhavcopyFormat.UDIFF: "%Y-%m-%d",
}


def bhavcopy_format(trade_date: date) -> BhavcopyFormat:
    """The layout NSE publishes for ``trade_date``."""
    return BhavcopyFormat.UDIFF if trade_date >= UDIFF_FROM else BhavcopyFormat.LEGACY


def bhavcopy_url(trade_date: date) -> str:
    """Archive URL of the bhavcopy for ``trade_date``."""
    if bhavcopy_format(trade_date) is BhavcopyFormat.UDIFF:
        return f"{ARCHIVE_BASE}/content/cm/BhavCopy_NSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.csv.zip"
    mon = trade_date.strftime("%b").upper()
    return (
        f"{ARCHIVE_BASE}/content/historical/EQUITIES/{trade_date.year}/{mon}/"
        f"cm{trade_date.day:02d}{mon}{trade_date.year}bhav.csv.zip"
    )


def classify_missing(
    trade_date: date,
    today: date,
    holidays: Iterable[date] | None = None,
    *,
    settle_days: int = SETTLE_DAYS,
) -> BhavcopyFileStatus:
    """What a 404 for ``trade_date`` means, seen on ``today`` (IST).

    * a listed exchange holiday -> ``NO_SESSION``;
    * old enough that the archive is settled -> ``NO_SESSION``;
    * otherwise the file may still be published -> ``PENDING`` (retry later).
    """
    if holidays is not None and trade_date in set(holidays):
        return BhavcopyFileStatus.NO_SESSION
    if (today - trade_date).days >= settle_days:
        return BhavcopyFileStatus.NO_SESSION
    return BhavcopyFileStatus.PENDING


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _number(raw: str | None) -> float | None:
    if raw is None or raw.strip() in ("", "-"):
        return None
    return float(raw)


def _required(row: dict[str, str], column: str) -> float:
    value = _number(row.get(column))
    if value is None:
        raise ValueError(f"{column} missing")
    return value


def _count(value: float, column: str) -> int:
    if not math.isfinite(value) or value != int(value):
        raise ValueError(f"{column} not a whole number")
    return int(value)


def _whole(value: float | None, column: str) -> int | None:
    return None if value is None else _count(value, column)


def parse_bhavcopy(
    trade_date: date,
    payload: bytes,
    *,
    series: frozenset[str] = EQUITY_SERIES,
) -> ParsedBhavcopy:
    """Parse a bhavcopy zip for ``trade_date``.

    Rows outside ``series``, and rows in those series whose ISIN is not a company's equity
    (``INE...``; ETFs trade in EQ with ``INF...``), are counted in ``skipped_series`` (the
    latter under ``"<series>:<ISIN prefix>"``). Rows that fail to parse or fail
    :func:`validate_ohlc`, and repeated (symbol, series) pairs, go to ``rejected`` with a
    reason. A file whose layout or session date is wrong raises :class:`ProviderError`: it is
    not the file that was asked for.
    """
    file_format = bhavcopy_format(trade_date)
    columns = _COLUMNS[file_format]
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if len(members) != 1:
                raise ProviderError(f"bhavcopy {trade_date}: expected one CSV, got {members}")
            text = archive.read(members[0]).decode("utf-8-sig")
    except zipfile.BadZipFile as exc:
        raise ProviderError(f"bhavcopy {trade_date}: not a zip archive") from exc

    reader = csv.DictReader(io.StringIO(text))
    header = {h.strip() for h in (reader.fieldnames or []) if h and h.strip()}
    missing = sorted(set(columns.values()) - header)
    if missing:
        raise ProviderError(f"bhavcopy {trade_date}: {file_format} layout lacks {missing}")

    rows: list[BhavcopyRow] = []
    rejected: list[RejectedBhavcopyRow] = []
    skipped: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    date_format = _DATE_FORMATS[file_format]

    for raw in reader:
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k and k.strip()}
        symbol, ser = row.get(columns["symbol"], ""), row.get(columns["series"], "")
        if ser not in series:
            skipped[ser] = skipped.get(ser, 0) + 1
            continue
        isin = row.get(columns["isin"], "")
        if isin and not isin.startswith(EQUITY_ISIN_PREFIX):
            # ETFs and other fund units trade in EQ too; they carry INF... ISINs.
            key = f"{ser}:{isin[:3]}"
            skipped[key] = skipped.get(key, 0) + 1
            continue

        try:
            stamped = datetime.strptime(row[columns["date"]], date_format).date()  # noqa: DTZ007
        except ValueError as exc:
            raise ProviderError(f"bhavcopy {trade_date}: bad session date {exc}") from exc
        if stamped != trade_date:
            raise ProviderError(f"bhavcopy {trade_date}: file is stamped {stamped}")

        def reject(reason: str, symbol: str = symbol, ser: str = ser) -> None:
            rejected.append(RejectedBhavcopyRow(trade_date, symbol, ser, reason))

        if not symbol or not row.get(columns["isin"]):
            reject("symbol or ISIN missing")
            continue
        if (symbol, ser) in seen:
            reject("duplicate symbol and series")
            continue
        seen.add((symbol, ser))

        try:
            o, h = _required(row, columns["open"]), _required(row, columns["high"])
            lo, c = _required(row, columns["low"]), _required(row, columns["close"])
            volume = _count(_required(row, columns["volume"]), columns["volume"])
            last = _number(row.get(columns["last"]))
            prev_close = _number(row.get(columns["prev_close"]))
            turnover = _number(row.get(columns["turnover"]))
            trades = _whole(_number(row.get(columns["trades"])), columns["trades"])
        except (ValueError, OverflowError) as exc:
            reject(f"unparseable: {exc}")
            continue

        check = validate_ohlc(o, h, lo, c, volume)
        if not check.is_valid:
            reject(check.reason or "invalid OHLC")
            continue
        rows.append(
            BhavcopyRow(
                trade_date=trade_date,
                symbol=symbol,
                series=ser,
                isin=row[columns["isin"]],
                open=o,
                high=h,
                low=lo,
                close=c,
                last=last,
                prev_close=prev_close,
                volume=volume,
                turnover=turnover,
                trades=trades,
            )
        )

    return ParsedBhavcopy(trade_date, file_format, rows, rejected, skipped)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BhavcopyDownload:
    """Result of asking for one day's file."""

    trade_date: date
    url: str
    status: Literal["OK", "NOT_FOUND"]
    payload: bytes | None = None
    sha256: str | None = None
    cache_path: Path | None = None
    from_cache: bool = False


class NseBhavcopyProvider:
    """Downloads bhavcopy files, caches them unchanged and rate-limits requests to NSE."""

    PROVIDER_NAME = "NSE_BHAVCOPY"

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        min_interval_s: float = 1.0,
        timeout_s: float = 30.0,
        session: requests.Session | Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._min_interval = min_interval_s
        self._timeout = timeout_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None
        if session is None:
            session = requests.Session()
            retries = Retry(
                total=3,
                backoff_factor=2.0,
                status_forcelist=[403, 429, 500, 502, 503, 504],
                allowed_methods=["GET"],
            )
            session.mount("https://", HTTPAdapter(max_retries=retries))
            session.headers.update(
                {"User-Agent": _USER_AGENT, "Accept": "*/*", "Referer": "https://www.nseindia.com/"}
            )
        self._session = session

    def cache_path(self, trade_date: date) -> Path:
        """Where the zip for ``trade_date`` is cached."""
        return self._cache_dir / str(trade_date.year) / Path(bhavcopy_url(trade_date)).name

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self._min_interval - (self._monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._monotonic()

    def _get(self, url: str) -> Any:
        self._throttle()
        try:
            return self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"NSE request failed: {url}: {exc}") from exc

    def fetch_day(self, trade_date: date, *, refresh: bool = False) -> BhavcopyDownload:
        """The zip for ``trade_date``, from the cache unless ``refresh`` or not cached yet.

        Raises :class:`ProviderError` for any answer other than 200 or 404.
        """
        url = bhavcopy_url(trade_date)
        path = self.cache_path(trade_date)
        if path.exists() and not refresh:
            payload = path.read_bytes()
            return BhavcopyDownload(
                trade_date, url, "OK", payload, _sha256(payload), path, from_cache=True
            )

        response = self._get(url)
        if response.status_code == 404:
            return BhavcopyDownload(trade_date, url, "NOT_FOUND")
        if response.status_code != 200:
            raise ProviderError(f"NSE bhavcopy {trade_date}: HTTP {response.status_code}")
        payload = bytes(response.content)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(payload)
        os.replace(tmp, path)
        return BhavcopyDownload(trade_date, url, "OK", payload, _sha256(payload), path)

    def get_trading_holidays(self) -> set[date]:
        """Capital-market holidays NSE lists (current calendar year only)."""
        response = self._get(HOLIDAY_URL)
        if response.status_code != 200:
            raise ProviderError(f"NSE holiday list: HTTP {response.status_code}")
        try:
            entries = response.json().get("CM", [])
            return {
                datetime.strptime(e["tradingDate"], "%d-%b-%Y").date()  # noqa: DTZ007
                for e in entries
            }
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ProviderError(f"NSE holiday list: unexpected payload: {exc}") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
