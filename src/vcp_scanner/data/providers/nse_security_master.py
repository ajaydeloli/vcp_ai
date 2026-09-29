"""NSE Security Master Provider (DATA_SPECIFICATION §14, §14A).

Fetches instrument listing/delisting/series data from NSE's official
equity information endpoints and archives.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.domain.market import SecurityRecord

logger = logging.getLogger(__name__)

# NSE date formats observed in various endpoints
_NSE_DATE_FORMATS = ["%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"]


def _parse_nse_date(raw: str | None) -> date | None:
    """Try multiple date formats commonly used by NSE."""
    if not raw or raw.strip() in ("-", "", "NA", "None"):
        return None
    raw = raw.strip()
    for fmt in _NSE_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    logger.warning("Could not parse NSE date: %s", raw)
    return None


class NSESecurityMasterProvider:
    """Fetches security master data from NSE website APIs.

    NSE exposes equity information via two main routes:
    1. The equity listing API (`/api/equity-stockIndices?index=...`)
       returns currently listed instruments with series, ISIN, etc.
    2. The equity info API (`/api/equity-meta-info?symbol=SYMBOL`)
       returns listing date, face value, and other metadata per symbol.

    For historical delistings, NSE maintains a separate archive page.
    This provider covers the primary live listing endpoint and the
    per-symbol metadata endpoint. The delisting archive is a separate
    spike item whose coverage must be verified (DATA_SPECIFICATION §14A).
    """

    PROVIDER_NAME = "NSE"
    BASE_URL = "https://www.nseindia.com"
    # Returns all EQ-segment stocks
    EQUITY_LIST_URL = (
        "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
    )
    EQUITY_L_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    FALLBACK_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

    def __init__(self) -> None:
        self._session = requests.Session()

        retries = Retry(
            total=3,
            backoff_factor=2.0,
            status_forcelist=[403, 429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retries))

        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
                "Referer": "https://www.nseindia.com/",
            }
        )

    def _init_session(self) -> None:
        """Fetch the homepage to obtain required session cookies."""
        try:
            self._session.get(self.BASE_URL, timeout=10)
        except Exception as e:
            logger.warning("Failed to initialize NSE session: %s", e)

    def get_security_history(self, start: date, end: date) -> list[SecurityRecord]:
        """Fetch all listed NSE equities from official EQUITY_L.csv archive."""
        import csv
        import io

        records: list[SecurityRecord] = []
        resp = None

        for url in (self.EQUITY_L_URL, self.FALLBACK_URL):
            try:
                resp = self._session.get(url, timeout=15)
                if resp.status_code == 200:
                    break
            except Exception as e:
                logger.warning("Failed to download from %s: %s", url, e)

        if not resp or resp.status_code != 200:
            logger.error("Could not download NSE EQUITY_L.csv from any archive source")
            return []

        try:
            reader = csv.DictReader(io.StringIO(resp.text))
            for raw_row in reader:
                # Strip keys and values because NSE CSV headers have leading whitespace
                row = {k.strip(): v.strip() for k, v in raw_row.items() if k and v}
                symbol = row.get("SYMBOL", "")
                if not symbol:
                    continue

                series = row.get("SERIES", "EQ")
                isin = row.get("ISIN NUMBER")
                listing_date = _parse_nse_date(row.get("DATE OF LISTING"))

                records.append(
                    SecurityRecord(
                        instrument_id=f"NSE_EQ|{symbol}",
                        symbol=symbol,
                        exchange="NSE",
                        valid_from=listing_date or start,
                        valid_to=None,
                        isin=isin,
                        series=series,
                        listing_date=listing_date,
                        delisting_date=None,
                        source=self.PROVIDER_NAME,
                    )
                )

            logger.info("NSE security master: loaded %d records from EQUITY_L.csv", len(records))
        except Exception as e:
            logger.error("Error parsing NSE EQUITY_L.csv: %s", e)

        return records

    def get_symbol_metadata(self, symbol: str) -> dict[str, Any] | None:
        """Fetch detailed metadata for a single symbol (listing date, etc).

        Uses the NSE equity-meta-info endpoint. This is rate-limited and
        should be called sparingly — only for enrichment or backfill.
        """
        self._init_session()

        url = f"{self.BASE_URL}/api/equity-meta-info?symbol={symbol}"
        try:
            resp = self._session.get(url, timeout=10)
            if resp.status_code != 200:
                logger.warning("NSE meta-info for %s failed: %s", symbol, resp.status_code)
                return None
            return resp.json()
        except Exception as e:
            logger.warning("NSE meta-info error for %s: %s", symbol, e)
            return None
