"""NSE financial-result filings: the old results feed and the integrated-filing feed (F1).

FUNDAMENTALS_SPECIFICATION §3. Two public feeds, same XBRL tag set:

* ``corporates-financial-results`` - filings to Jan 2025 (``period`` Quarterly | Annual).
* ``integrated-filing-results`` - filings from Feb 2025. One feed for several kinds of filing;
  only ``Integrated Filing- Financials`` rows are results. It returns at most 20 rows per query
  (verified 2026-10-03), so ``FilingListing.truncated`` is set when 20 rows come back.

One request per second at most; HTTP 403/429 and timeouts are retried with a growing pause and a
fresh session cookie. Nothing is parsed here.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import requests

from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.providers.nse_http import nse_user_agent
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.fundamentals.base import (
    FEED_INTEGRATED,
    FEED_OLD,
    FilingListing,
    FilingRef,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nseindia.com"
OLD_URL = f"{BASE_URL}/api/corporates-financial-results"
NEW_URL = f"{BASE_URL}/api/integrated-filing-results"
NEW_FEED_ROW_LIMIT = 20
_FINANCIALS = "Integrated Filing- Financials"
_BACKOFF_SECONDS = (5.0, 20.0, 60.0)


def _dt(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _basis(text: str | None) -> str:
    t = (text or "").strip().lower()
    if t == "consolidated":
        return "CONSOLIDATED"
    if t in ("non-consolidated", "standalone"):
        return "STANDALONE"
    return "UNKNOWN"


def _audited(text: str | None) -> bool | None:
    t = (text or "").strip().lower()
    if t == "audited":
        return True
    if t in ("un-audited", "unaudited", "limited review"):
        return False
    return None


def parse_old_row(row: dict[str, Any]) -> FilingRef | None:
    """A ``corporates-financial-results`` row, or None when it has no usable XBRL file."""
    period_end = _dt(row.get("toDate"))
    broadcast = _dt(row.get("broadCastDate"))
    url = row.get("xbrl")
    # Filings before about 2013 have no XBRL file; the feed links ".../corporate/xbrl/-"
    # (verified 2026-10-10). A real file ends in .xml.
    if not (isinstance(url, str) and url.startswith("http") and url.lower().endswith(".xml")):
        return None
    if not (period_end and broadcast and row.get("symbol") and row.get("seqNumber")):
        return None
    kind = {"Quarterly": "QUARTER", "Annual": "ANNUAL"}.get(str(row.get("period")))
    return FilingRef(
        source_feed=FEED_OLD,
        source_record_id=str(row["seqNumber"]),
        symbol=str(row["symbol"]),
        isin=row.get("isin"),
        period_end=period_end.date(),
        period_type=kind,
        statement_basis=_basis(row.get("consolidated")),
        audited=_audited(row.get("audited")),
        broadcast_at=broadcast,
        url=str(url),
        revision_flags=f"reInd={row.get('reInd')};oldNewFlag={row.get('oldNewFlag')}",
        meta={"relatingTo": str(row.get("relatingTo") or ""), "bank": str(row.get("bank") or "")},
    )


def parse_new_row(row: dict[str, Any]) -> FilingRef | None:
    """An ``integrated-filing-results`` row of type Financials, else None."""
    if row.get("type") != _FINANCIALS:
        return None
    period_end = _dt(row.get("qe_Date"))
    broadcast = _dt(row.get("broadcast_Date"))
    url = row.get("xbrl")
    if not (period_end and broadcast and url and row.get("symbol") and row.get("seq_Id")):
        return None
    return FilingRef(
        source_feed=FEED_INTEGRATED,
        source_record_id=str(row["seq_Id"]),
        symbol=str(row["symbol"]),
        isin=None,
        period_end=period_end.date(),
        period_type=None,  # the feed does not say quarterly or annual: decided when parsed
        statement_basis=_basis(row.get("consolidated")),
        audited=_audited(row.get("audited")),
        broadcast_at=broadcast,
        url=str(url),
        revision_flags=(
            f"type_Sub={row.get('type_Sub')};revised_Date={row.get('revised_Date')};"
            f"remark={row.get('revision_Remark')}"
        ),
    )


class NseFilingProvider:
    """Lists and downloads NSE result filings."""

    def __init__(
        self,
        *,
        interval_seconds: float = 1.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 40.0,
    ) -> None:
        self._interval = interval_seconds
        self._sleep = sleep
        self._timeout = timeout
        self._last = 0.0
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": nse_user_agent(),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._primed = False

    def _prime(self) -> None:
        try:
            self._session.get(BASE_URL, timeout=15)
        except requests.RequestException as exc:
            logger.warning("NSE session cookie request failed: %s", exc)
        self._primed = True

    def _get(self, url: str, params: dict[str, str] | None = None) -> requests.Response:
        if not self._primed:
            self._prime()
        last_error = "no attempt"
        for attempt in range(len(_BACKOFF_SECONDS) + 1):
            wait = self._interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    return response
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in (403, 429, 500, 502, 503, 504):
                    break
            if attempt < len(_BACKOFF_SECONDS):
                self._sleep(_BACKOFF_SECONDS[attempt])
                self._prime()
        raise ProviderError(f"NSE request failed ({last_error}): {url}")

    @staticmethod
    def _range(start: date | None, end: date | None) -> dict[str, str]:
        if start is None or end is None:
            return {}
        return {"from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")}

    def list_filings(
        self,
        *,
        symbol: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> FilingListing:
        base: dict[str, str] = {"index": "equities", **self._range(start, end)}
        if symbol:
            base["symbol"] = symbol
        refs: list[FilingRef] = []
        for period in ("Quarterly", "Annual"):
            rows = self._json_rows(self._get(OLD_URL, {**base, "period": period}))
            refs.extend(r for r in map(parse_old_row, rows) if r is not None)
        new_rows = self._json_rows(self._get(NEW_URL, base))
        refs.extend(r for r in map(parse_new_row, new_rows) if r is not None)
        # Rows of other kinds count toward the cap, so compare the raw row count.
        truncated = len(new_rows) >= NEW_FEED_ROW_LIMIT
        unique = {r.filing_id: r for r in refs}
        return FilingListing(
            sorted(unique.values(), key=lambda r: (r.symbol, r.broadcast_at)), truncated
        )

    @staticmethod
    def _json_rows(response: requests.Response) -> list[dict[str, Any]]:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"NSE returned non-JSON: {response.url}") from exc
        rows = (
            data
            if isinstance(data, list)
            else data.get("data", [])
            if isinstance(data, dict)
            else []
        )
        return [r for r in rows if isinstance(r, dict)]

    def download(self, url: str) -> bytes:
        return self._get(url).content
