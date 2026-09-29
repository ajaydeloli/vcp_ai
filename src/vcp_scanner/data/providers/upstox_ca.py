"""Upstox Corporate Actions Provider (DATA_SPECIFICATION §18A)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.data.identity import deterministic_action_id
from vcp_scanner.domain.corporate_actions import CorporateAction
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.market import Instrument
from vcp_scanner.infrastructure.clock import Clock, utc_now

logger = logging.getLogger(__name__)


class UpstoxCorporateActionProvider:
    """Fetches corporate actions from Upstox API."""

    PROVIDER_NAME = "UPSTOX"
    BASE_URL = "https://api.upstox.com/v2"

    def __init__(self, access_token: str | None = None, *, clock: Clock = utc_now) -> None:
        self._access_token = access_token
        self._clock = clock
        self._session = requests.Session()

        # Configure retries
        retries = Retry(
            total=3,
            backoff_factor=1.0,
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

                response = self._session.get(url, timeout=10)

                # If unauthorized or not found, we might want to log and continue
                if response.status_code in (401, 403):
                    logger.error(f"Upstox API authentication failed: {response.text}")
                    break

                if response.status_code != 200:
                    logger.warning(
                        "Failed to fetch Upstox actions for %s: %s %s",
                        instrument.symbol,
                        response.status_code,
                        response.text,
                    )
                    continue

                data = response.json().get("data", [])
                for item in data:
                    action = self._parse_upstox_action(instrument, item)
                    if action and action.ex_date and start <= action.ex_date <= end:
                        actions.append(action)

            except Exception as e:
                logger.error(f"Error fetching Upstox actions for {instrument.symbol}: {e}")

        return actions

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

            # Upstox usually provides a ratio string like "1:2"
            ratio_str = item.get("ratio") or ""  # null for dividends
            num, den = None, None
            if ":" in ratio_str:
                parts = ratio_str.split(":")
                num = float(parts[0])
                den = float(parts[1])

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
