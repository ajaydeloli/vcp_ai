"""NSE shareholding-pattern filings: the listing and the XBRL files (F8).

FUNDAMENTALS_SPECIFICATION §15. The listing (``api/corporate-share-holdings-master``) is on
NSE's website, so it goes through the website client and its pause; the XBRL files are on the
archive host (``nsearchives.nseindia.com``) and go through a second client with its own pause.
Both reuse the results provider's retries and wall-clock limit. Nothing is parsed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.providers.nse_filings import BASE_URL, NseFilingProvider
from vcp_scanner.domain.errors import ProviderError

LIST_URL = f"{BASE_URL}/api/corporate-share-holdings-master"
FEED = "NSE_SHP"


@dataclass(frozen=True)
class ShpRef:
    """One shareholding filing as NSE lists it."""

    record_id: str
    symbol: str
    as_on: date  # the date the pattern is as on (a quarter end, or an event date)
    broadcast_at: datetime  # IST: when the market could first see it
    revised: bool
    new_layout: bool | None  # NSE's ``newColumn``
    url: str
    promoter_pct: float | None  # NSE's listing value, kept to cross-check the parse
    public_pct: float | None

    @property
    def filing_id(self) -> str:
        return f"{FEED}:{self.record_id}"


def _dt(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(text.strip().title(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _f(text: Any) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_listing(rows: list[dict[str, Any]]) -> tuple[list[ShpRef], int]:
    """Rows of the listing -> filings; also the number of rows skipped (no file, no date)."""
    out: list[ShpRef] = []
    skipped = 0
    for r in rows:
        url = str(r.get("xbrl") or "").strip()
        as_on = _dt(r.get("date"))
        when = (
            _dt(r.get("broadcastDate")) or _dt(r.get("systemDate")) or _dt(r.get("submissionDate"))
        )
        if not url.startswith("http") or as_on is None or when is None or not r.get("recordId"):
            skipped += 1
            continue
        out.append(ShpRef(
            record_id=str(r["recordId"]), symbol=str(r.get("symbol") or "").strip().upper(),
            as_on=as_on.date(), broadcast_at=when,
            revised=str(r.get("revisedData") or "N").upper() not in ("N", "", "-")
            or str(r.get("revisedStatus") or "").lower() == "revised",
            new_layout=r.get("newColumn") if isinstance(r.get("newColumn"), bool) else None,
            url=url, promoter_pct=_f(r.get("pr_and_prgrp")), public_pct=_f(r.get("public_val")),
        ))  # fmt: skip
    return out, skipped


class NseShareholdingProvider:
    def __init__(self, *, site_interval_seconds: float = 2.0,
                 archive_interval_seconds: float = 1.0) -> None:  # fmt: skip
        self._site = NseFilingProvider(interval_seconds=site_interval_seconds)
        self._archive_interval = archive_interval_seconds
        self._archive = self._new_archive()

    def _new_archive(self) -> NseFilingProvider:
        # A file is ~0.5 MB: 20 s per wait and 45 s in all are plenty. The results client's
        # 180 s limit, four tries each, let one stalled connection hold a run for 13 minutes.
        return NseFilingProvider(interval_seconds=self._archive_interval, timeout=20.0,
                                 total_timeout=45.0)  # fmt: skip

    def reset(self) -> None:
        """Drop the archive connection (after a failure) and start a fresh one."""
        self._archive = self._new_archive()

    def _list(self, params: dict[str, str]) -> list[ShpRef]:
        response = self._site._get(LIST_URL, {"index": "equities", **params})
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"NSE shareholding listing is not JSON: {exc}") from exc
        rows = body if isinstance(body, list) else body.get("data", [])
        if not isinstance(rows, list):
            raise ProviderError("NSE shareholding listing has no rows")
        refs, _ = parse_listing(rows)
        return refs

    def list_range(self, start: date, end: date) -> list[ShpRef]:
        """Filings broadcast in [start, end] (NSE's ``from_date`` / ``to_date``)."""
        return self._list({"from_date": start.strftime("%d-%m-%Y"),
                           "to_date": end.strftime("%d-%m-%Y")})  # fmt: skip

    def list_symbol(self, symbol: str) -> list[ShpRef]:
        """Every filing of one stock NSE still lists (back to 2021 at the probe)."""
        return self._list({"symbol": symbol})

    def download(self, url: str) -> bytes:
        return bytes(self._archive._get(url).content)
