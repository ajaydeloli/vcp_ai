"""Upstox Corporate Actions Provider (DATA_SPECIFICATION §18A)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.data.identity import deterministic_action_id
from vcp_scanner.domain.corporate_actions import CorporateAction
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.errors import ProviderAuthError, ProviderError
from vcp_scanner.domain.market import Instrument
from vcp_scanner.infrastructure.clock import Clock, utc_now

logger = logging.getLogger(__name__)


class UpstoxCorporateActionProvider:
    """Fetches corporate actions from Upstox API."""

    PROVIDER_NAME = "UPSTOX"
    BASE_URL = "https://api.upstox.com/v2"

    def __init__(
        self,
        access_token: str | None = None,
        *,
        clock: Clock = utc_now,
        min_request_interval_seconds: float = 0.25,
        max_failure_share: float = 0.05,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._access_token = access_token
        self._clock = clock
        # Pacing (owner-approved fix, 2026-10-01): one request per instrument, ~2,600 per run.
        # Unpaced (~5 req/s) the 22:00 run drew HTTP 429 for 30 instruments. Upstox documents
        # 25/s and 250/min per API; 0.25 s keeps a run under 250/min (~11 min for 2,600).
        self._min_interval = min_request_interval_seconds
        self._max_failure_share = max_failure_share
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None
        #: "SYMBOL: reason" for instruments whose fetch failed in the last ``get_actions`` call
        #: when their share stayed within ``max_failure_share``. They are not in
        #: ``queried_instrument_ids``, so reconciliation treats them as NSE-only.
        self.failed: list[str] = []
        self._session = requests.Session()
        #: Instrument ids actually queried by the last ``get_actions`` call (HTTP 200, or 404 =
        #: "no record"). Reconciliation treats Upstox's silence about a split/bonus as
        #: evidence only for these instruments (audit P0-2).
        self.queried_instrument_ids: set[str] = set()
        #: Earliest ex-date Upstox returned per queried instrument, over ALL its records (before
        #: the date-window filter). Upstox only serves about the last 12 months of events, so
        #: its silence is evidence only from this date on (found in audit Fix 5b). An
        #: instrument with no records has no entry: its silence proves nothing.
        self.coverage_start: dict[str, date] = {}

        # Configure retries
        # Back off 2, 4, 8, 16 s; a 429's Retry-After header is honoured (urllib3 default).
        retries = Retry(
            total=5,
            backoff_factor=2.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retries))

        if access_token:
            self._session.headers.update(
                {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            )

    def get_actions(
        self, start: date, end: date, instruments: list[Instrument] | None = None
    ) -> list[CorporateAction]:
        """Fetch corporate actions reported by Upstox.

        Uses the Upstox Fundamentals corporate-actions endpoint, keyed by ISIN.
        """
        if not instruments:
            logger.warning(
                "Upstox CorporateActionProvider requires a list of instruments to query."
            )
            return []

        actions: list[CorporateAction] = []
        failures: list[str] = []
        self.queried_instrument_ids = set()
        self.coverage_start = {}
        self.failed = []
        attempted = 0

        for instrument in instruments:
            # Upstox keys equities by ISIN ("NSE_EQ|<ISIN>"), never by trading symbol
            # (DATA_SPECIFICATION section 6, Identity). Without an ISIN there is no reliable key.
            if not instrument.isin:
                logger.warning(
                    "Skipping Upstox actions for %s: no ISIN to build an instrument key.",
                    instrument.instrument_id,
                )
                continue

            try:
                # Upstox Fundamentals API: GET /v2/fundamentals/{isin}/corporate-actions
                # (https://upstox.com/developer/api-documentation/get-corporate-actions).
                # It takes no date parameters and returns every event for the ISIN, so the
                # [start, end] window is applied client-side on the ex-date below.
                url = f"{self.BASE_URL}/fundamentals/{instrument.isin}/corporate-actions"

                self._pace()
                attempted += 1
                response = self._session.get(url, timeout=10)

                # Bad credentials fail every remaining request: stop, do not return a
                # partial list that looks like a complete one.
                if response.status_code in (401, 403):
                    raise ProviderAuthError(
                        f"Upstox API authentication failed: HTTP {response.status_code} "
                        f"{response.text[:200]}"
                    )

                # 404 = Upstox has no record for this ISIN, which is a legitimate "no actions".
                if response.status_code == 404:
                    logger.info("Upstox has no corporate-action record for %s", instrument.symbol)
                    self.queried_instrument_ids.add(instrument.instrument_id)
                    continue

                if response.status_code != 200:
                    failures.append(f"{instrument.symbol}: HTTP {response.status_code}")
                    continue

                data = response.json().get("data", [])
                self.queried_instrument_ids.add(instrument.instrument_id)
                for item in data:
                    seen = _parse_date(item.get("expiry_date"))
                    if seen is not None:
                        held = self.coverage_start.get(instrument.instrument_id)
                        if held is None or seen < held:
                            self.coverage_start[instrument.instrument_id] = seen
                    action = self._parse_upstox_action(instrument, item)
                    if action and action.ex_date and start <= action.ex_date <= end:
                        actions.append(action)

            except ProviderError:
                raise
            except Exception as e:
                failures.append(f"{instrument.symbol}: {e}")

        if failures:
            preview = "; ".join(failures[:5])
            summary = (
                f"Upstox corporate actions failed for {len(failures)} of "
                f"{len(instruments)} instruments (first: {preview})"
            )
            # A few failures (rate limits, timeouts) are tolerated: the failed instruments are
            # not in ``queried_instrument_ids``, so Upstox's silence about them is never used
            # as evidence and their actions stay NSE-only for this run. Beyond the share the
            # source is treated as broken and the run fails (ingestion is idempotent).
            if len(failures) > self._max_failure_share * max(attempted, 1):
                raise ProviderError(summary)
            logger.warning("%s; continuing with the rest (NSE only for these).", summary)
            self.failed = failures

        return actions

    def _pace(self) -> None:
        """Keep at least ``min_request_interval_seconds`` between requests."""
        now = self._monotonic()
        if self._last_request is not None:
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                self._sleep(wait)
                now = self._monotonic()
        self._last_request = now

    def _parse_upstox_action(
        self, instrument: Instrument, item: dict[str, Any]
    ) -> CorporateAction | None:
        """Parse a single Upstox JSON record into a CorporateAction."""
        try:
            raw_type = str(item.get("name", "")).upper()
            action_type = None

            # Map Upstox types to our domain enum
            if "SPLIT" in raw_type:
                action_type = CorporateActionType.SPLIT
            elif "BONUS" in raw_type:
                action_type = CorporateActionType.BONUS
            elif "DIVIDEND" in raw_type:
                action_type = CorporateActionType.DIVIDEND
            elif "RIGHTS" in raw_type:
                action_type = CorporateActionType.RIGHTS
            else:
                # Ignore types we don't care about
                return None

            # ``expiry_date`` is the ex-date / effective date, formatted "14 Aug 2025".
            ex_date_str = item.get("expiry_date")
            ex_date = (
                datetime.strptime(ex_date_str, "%d %b %Y").replace(tzinfo=UTC).date()
                if ex_date_str
                else None
            )

            # Upstox ratio strings: SPLIT "old:new" in shares (KOTAKBANK's face-value 5 -> 1
            # split is "1:5"); BONUS "bonus:held" ("1:1"). The adjustment engine and the NSE
            # parser use SPLIT = (old face value, new face value), which is the reverse of the
            # share ratio, so a split's parts are swapped (found on live data, audit Fix 5b).
            ratio_str = item.get("ratio") or ""  # null for dividends
            num, den = None, None
            if ":" in ratio_str:
                parts = ratio_str.split(":")
                num = float(parts[0])
                den = float(parts[1])
                if action_type == CorporateActionType.SPLIT:
                    num, den = den, num

            cash_amount = float(item["amount"]) if item.get("amount") is not None else None

            # Deterministic ID: the same Upstox record maps to the same row on every fetch.
            action_id = deterministic_action_id(
                self.PROVIDER_NAME,
                instrument.isin or instrument.instrument_id,
                action_type.value,
                ex_date,
                num,
                den,
                cash_amount,
            )

            return CorporateAction(
                corporate_action_id=action_id,
                instrument_id=instrument.instrument_id,
                isin=instrument.isin,
                action_type=action_type,
                source=self.PROVIDER_NAME,
                created_at=self._clock(),
                ex_date=ex_date,
                ratio_numerator=num,
                ratio_denominator=den,
                cash_amount=cash_amount,
                source_record_id=None,
            )
        except Exception as e:
            logger.warning(f"Could not parse Upstox record {item}: {e}")
            return None


def _parse_date(raw: object) -> date | None:
    """Upstox ``expiry_date`` ("14 Aug 2025") -> date; None if absent or unreadable."""
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%d %b %Y").replace(tzinfo=UTC).date()
    except ValueError:
        return None
