"""NSE Surveillance Flags Provider (PROJECT_DESIGN §14A).

Fetches ASM (Additional Surveillance Measure), GSM (Graded Surveillance
Measure), T2T (Trade-to-Trade), and BE (Book Entry) flags from NSE's
official surveillance endpoints.
"""

from __future__ import annotations

import logging
from datetime import date

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.data.identity import mint_instrument_id
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import SurveillanceRecord
from vcp_scanner.infrastructure.clock import Clock, utc_now

logger = logging.getLogger(__name__)


class NSESurveillanceProvider:
    """Fetches surveillance flags from NSE website APIs.

    NSE publishes surveillance lists at several endpoints:
    - ASM list: /api/reportASM  (or /api/merged-daily-reports-capital)
    - GSM list: published as PDFs/Excel — harder to parse automatically
    - T2T/BE: available in the bhavcopy series field (series='BE' or 'BT')

    This provider attempts to fetch from the known JSON API endpoints.
    For GSM specifically, manual upload or a separate PDF parser spike
    may be needed (PROJECT_DESIGN §14A verification item).
    """

    PROVIDER_NAME = "NSE"
    # get_flags ignores its window and returns the full set active today, so a flag
    # missing from the feed really has lapsed (the worker relies on this to close flags).
    returns_active_snapshot = True
    BASE_URL = "https://www.nseindia.com"

    # Known API routes for surveillance data
    ASM_URL = "https://www.nseindia.com/api/reportASM?index=equities"
    SEC_LIST_URL = "https://nsearchives.nseindia.com/content/equities/sec_list.csv"

    def __init__(self, *, clock: Clock = utc_now) -> None:
        # ``valid_from`` of a returned flag is the clock's date: the feed is a live snapshot
        # of what is active now, so it can never be backdated to an earlier as-of date.
        self._clock = clock
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

    def get_flags(self, start: date, end: date) -> list[SurveillanceRecord]:
        """Fetch current surveillance flags from NSE."""
        records: list[SurveillanceRecord] = []

        records.extend(self._fetch_asm_flags())
        records.extend(self._fetch_t2t_flags())

        logger.info("NSE surveillance: fetched %d flag records", len(records))
        return records

    def _fetch_asm_flags(self) -> list[SurveillanceRecord]:
        """Fetch ASM (Additional Surveillance Measure) flagged stocks."""
        self._init_session()
        records: list[SurveillanceRecord] = []

        try:
            resp = self._session.get(self.ASM_URL, timeout=15)
            if resp.status_code != 200:
                raise ProviderError(
                    f"NSE ASM fetch failed: HTTP {resp.status_code} {resp.text[:200]}"
                )

            data = resp.json()
            # NSE reportASM returns dict with 'longterm' and 'shortterm' keys
            items = []
            if isinstance(data, dict):
                items.extend(data.get("longterm", {}).get("data", []))
                items.extend(data.get("shortterm", {}).get("data", []))
            elif isinstance(data, list):
                items = data

            today = self._clock().date()
            for item in items:
                symbol = item.get("symbol", "")
                if not symbol:
                    continue

                stage = item.get("asmSurvIndicator") or item.get("stage", "")
                surv_desc = item.get("survDesc", "")

                records.append(
                    SurveillanceRecord(
                        instrument_id=mint_instrument_id("NSE", symbol),
                        flag="ASM",
                        valid_from=today,
                        valid_to=None,
                        source=self.PROVIDER_NAME,
                        extra={"stage": str(stage), "description": str(surv_desc)},
                    )
                )

        except ProviderError:
            raise
        except Exception as e:
            # The caller treats this list as the full active set and closes every flag that
            # is missing from it, so a swallowed failure would silently clear real flags.
            raise ProviderError(f"NSE ASM fetch error: {e}") from e

        return records

    def _fetch_t2t_flags(self) -> list[SurveillanceRecord]:
        """Identify T2T (Trade-to-Trade / BE / BT) stocks from sec_list.csv."""
        import csv
        import io

        records: list[SurveillanceRecord] = []

        try:
            resp = self._session.get(self.SEC_LIST_URL, timeout=15)
            if resp.status_code != 200:
                raise ProviderError(f"NSE sec_list.csv fetch failed: HTTP {resp.status_code}")

            reader = csv.DictReader(io.StringIO(resp.text))
            today = self._clock().date()

            for raw_row in reader:
                row = {k.strip(): v.strip() for k, v in raw_row.items() if k and v}
                symbol = row.get("Symbol", "")
                series = row.get("Series", "")
                band = row.get("Band", "")
                if series in ("BE", "BT", "BZ"):
                    records.append(
                        SurveillanceRecord(
                            instrument_id=mint_instrument_id("NSE", symbol),
                            flag="T2T",
                            valid_from=today,
                            valid_to=None,
                            source=self.PROVIDER_NAME,
                            extra={"series": series, "band": band},
                        )
                    )

        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"NSE T2T fetch error: {e}") from e

        return records
