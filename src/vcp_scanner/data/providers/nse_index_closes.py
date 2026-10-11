"""NSE's daily closing values of every index (FRONTEND_SPECIFICATION §67.28, Market Overview).

One CSV a session on the archive host (the same host as the bhavcopy, not the rate-limited
website): ``https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv``.
Probed 2026-10-10/11: 168 indices, columns ``Index Name, Index Date, Open/High/Low/Closing Index
Value, Points Change, Change(%), Volume, Turnover (Rs. Cr.), P/E, P/B, Div Yield``; 404 on a day
without a session. Display only: nothing here feeds a scan, a score or the paper ledger.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import requests

from vcp_scanner.data.providers.nse_http import nse_user_agent
from vcp_scanner.domain.errors import ProviderError

URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{:%d%m%Y}.csv"


@dataclass(frozen=True)
class IndexClose:
    index_name: str  # as NSE writes it, e.g. "Nifty 50"
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None
    turnover_cr: float | None
    pe: float | None
    pb: float | None
    div_yield: float | None


@dataclass(frozen=True)
class IndexFile:
    trade_date: date
    status: Literal["OK", "NOT_FOUND"]
    payload: bytes | None = None
    sha256: str | None = None
    from_cache: bool = False


def _num(raw: str | None) -> float | None:
    text = (raw or "").strip().replace(",", "")
    if text in ("", "-", "NA", "N.A."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_index_closes(payload: bytes, trade_date: date) -> list[IndexClose]:
    """Every index of one day's file. A row of another date, or without a close, is skipped;
    a file with no row of ``trade_date`` at all is an error (a wrong or changed file)."""
    text = payload.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    out: list[IndexClose] = []
    for row in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        name = row.get("Index Name", "")
        try:
            day = datetime.strptime(row.get("Index Date", ""), "%d-%m-%Y").date()  # noqa: DTZ007
        except ValueError:
            continue
        close = _num(row.get("Closing Index Value"))
        if not name or day != trade_date or close is None:
            continue
        out.append(IndexClose(
            index_name=name, trade_date=day, open=_num(row.get("Open Index Value")),
            high=_num(row.get("High Index Value")), low=_num(row.get("Low Index Value")),
            close=close, volume=_num(row.get("Volume")),
            turnover_cr=_num(row.get("Turnover (Rs. Cr.)")), pe=_num(row.get("P/E")),
            pb=_num(row.get("P/B")), div_yield=_num(row.get("Div Yield")),
        ))  # fmt: skip
    if not out:
        raise ProviderError(f"NSE index closes {trade_date}: no index rows for that date")
    return out


class NseIndexCloseProvider:
    """Downloads the daily index file, keeps it unchanged in a cache, one request a second."""

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
        self._last: float | None = None
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": nse_user_agent(), "Accept": "*/*"})
        self._session = session

    def cache_path(self, trade_date: date) -> Path:
        return self._cache_dir / str(trade_date.year) / Path(URL.format(trade_date)).name

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self._min_interval - (self._monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._monotonic()

    def fetch_day(self, trade_date: date) -> IndexFile:
        """The file of ``trade_date``, from the cache when it is there. Raises
        :class:`ProviderError` for any answer other than 200 or 404."""
        path = self.cache_path(trade_date)
        if path.exists():
            payload = path.read_bytes()
            return IndexFile(trade_date, "OK", payload, _sha256(payload), from_cache=True)
        self._throttle()
        url = URL.format(trade_date)
        try:
            response = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"NSE index closes {trade_date}: {exc}") from exc
        if response.status_code == 404:
            return IndexFile(trade_date, "NOT_FOUND")
        if response.status_code != 200:
            raise ProviderError(f"NSE index closes {trade_date}: HTTP {response.status_code}")
        payload = bytes(response.content)
        parse_index_closes(payload, trade_date)  # a broken file is not cached
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(payload)
        os.replace(tmp, path)
        return IndexFile(trade_date, "OK", payload, _sha256(payload))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
