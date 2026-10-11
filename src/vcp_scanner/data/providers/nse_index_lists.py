"""Sector of each stock from NSE's index constituent lists (DATA_SPECIFICATION §48.1, step S1).

The lists live on the archive host (not the rate-limited website): ``Company Name, Industry,
Symbol, Series, ISIN Code``. The Nifty Total Market list holds about 755 stocks, the Microcap 250
already inside it (probed 2026-10-10). Its ``Industry`` column is NSE's *sector* level of the
four-level classification (macro-economic sector, sector, industry, basic industry), e.g.
"Capital Goods", "Financial Services".
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass

import requests

from vcp_scanner.data.providers.nse_http import nse_user_agent
from vcp_scanner.domain.errors import ProviderError

ARCHIVE = "https://nsearchives.nseindia.com/content/indices"
#: Lists read, broadest first; a later list never overrides an earlier one.
INDEX_LISTS: tuple[str, ...] = ("ind_niftytotalmarket_list.csv",)


@dataclass(frozen=True)
class IndexListEntry:
    symbol: str
    isin: str | None
    company: str
    sector: str  # the list's "Industry" column = NSE's sector level
    source_list: str


def parse_index_list(text: str, source_list: str) -> list[IndexListEntry]:
    out: list[IndexListEntry] = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        symbol = (row.get("Symbol") or "").strip()
        sector = (row.get("Industry") or "").strip()
        if not symbol or not sector or sector.lower() == "industry":
            continue
        out.append(IndexListEntry(
            symbol=symbol, isin=(row.get("ISIN Code") or "").strip() or None,
            company=(row.get("Company Name") or "").strip(), sector=sector,
            source_list=source_list,
        ))  # fmt: skip
    return out


class NseIndexListProvider:
    def __init__(self, *, session: requests.Session | None = None, pause: float = 2.0) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": nse_user_agent(), "Accept": "text/csv,*/*"})
        self._pause = pause

    def entries(self, lists: tuple[str, ...] = INDEX_LISTS) -> list[IndexListEntry]:
        seen: dict[str, IndexListEntry] = {}
        for n, name in enumerate(lists):
            if n:
                time.sleep(self._pause)
            try:
                response = self._session.get(f"{ARCHIVE}/{name}", timeout=30)
            except requests.RequestException as exc:
                raise ProviderError(f"NSE index list {name}: {exc}") from exc
            if response.status_code != 200:
                raise ProviderError(f"NSE index list {name}: HTTP {response.status_code}")
            for e in parse_index_list(response.text, name):
                seen.setdefault(e.symbol, e)
        return list(seen.values())
