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
        min_request_interval_seconds: float = 1.9,
        max_failure_share: float = 0.05,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._access_token = access_token
        self._clock = clock
        # Pacing (owner decision 2026-10-01): Upstox documents 25/s, 250/min and 1,000 per
        # 30 min per API, and enforced the 30-minute limit after the unpaced 22:00 run. 1.9 s
        # between requests stays under all three (~950 per 30 min).
        self._min_interval = min_request_interval_seconds
        self._max_failure_share = max_failure_share
        # Resolved at construction, so tests can swap the module default for a no-op.
        self._sleep = sleep if sleep is not None else _default_sleep
        self._monotonic = monotonic
        self._last_request: float | None = None
        #: "SYMBOL: reason" for instruments whose fetch failed in the last ``get_actions`` call
        #: when their share stayed within ``max_failure_share``. They are not in
        #: ``queried_instrument_ids``, so reconciliation treats them as NSE-only.
        self.failed: list[str] = []
        #: Set when Upstox kept answering HTTP 429 (rate limit) and the run stopped asking;
        #: the instruments not reached are simply not queried (NSE-only this run).
        self.rate_limited: str | None = None
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
        # Server errors: back off 2, 4, 8 s. HTTP 429 is handled in ``get_actions`` (one wait,
        # then stop), so a rate limit cannot stretch a run into hours of retries.
        retries = Retry(
            total=3,
            backoff_factor=2.0,
            status_forcelist=[500, 502, 503, 504],
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
        self.rate_limited = None
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
                if response.status_code == 429:
                    # Wait once (Retry-After, capped), then stop asking for this run.
                    self._sleep(_retry_after_seconds(response))
                    self._last_request = self._monotonic()
                    response = self._session.get(url, timeout=10)
                    if response.status_code == 429:
                        attempted -= 1
                        self.rate_limited = (
                            f"Upstox rate limit (HTTP 429) after {attempted} of "
                            f"{len(instruments)} instruments"
                        )
                        logger.warning("%s; the rest stay NSE-only this run.", self.rate_limited)
                        break

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

            # Upstox sends amount 0.0 on splits, bonuses and rights (live check 2026-10-02):
            # "not reported", not a zero issue price. Read as 0 it disagreed with NSE's rights
            # issue price and turned every rights issue into a PROVIDER_CONFLICT (no factor).
            raw_amount = item.get("amount")
            cash_amount = float(raw_amount) if raw_amount is not None else None
            if cash_amount is not None and cash_amount <= 0:
                cash_amount = None

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


def _retry_after_seconds(response: Any, default: float = 30.0, cap: float = 60.0) -> float:
    """Seconds to wait after HTTP 429: the Retry-After header (seconds) if given, capped."""
    raw = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(value, cap))


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)
