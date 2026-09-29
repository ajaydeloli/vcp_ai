"""NSE Corporate Actions Provider (DATA_SPECIFICATION §18A)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.data.identity import deterministic_action_id, mint_instrument_id
from vcp_scanner.domain.corporate_actions import CorporateAction
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.market import Instrument

logger = logging.getLogger(__name__)


class NSECorporateActionProvider:
    """Fetches corporate actions from official NSE website."""

    PROVIDER_NAME = "NSE"
    BASE_URL = "https://www.nseindia.com"
    API_URL = "https://www.nseindia.com/api/corporates-corporateActions?index=equities"

    def __init__(self) -> None:
        self._session = requests.Session()

        # Configure retries and realistic headers to bypass basic anti-scraping
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
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def _init_session(self) -> None:
        """Fetch the homepage to get the required session cookies."""
        try:
            self._session.get(self.BASE_URL, timeout=10)
        except Exception as e:
            logger.warning(f"Failed to initialize NSE session cookies: {e}")

    def get_actions(
        self, start: date, end: date, instruments: list[Instrument] | None = None
    ) -> list[CorporateAction]:
        """Fetch corporate actions reported by NSE in the date range."""
        self._init_session()

        actions: list[CorporateAction] = []

        # Format dates as DD-MM-YYYY for NSE API
        params = {"from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")}

        try:
            response = self._session.get(self.API_URL, params=params, timeout=15)

            if response.status_code != 200:
                logger.error(
                    f"Failed to fetch NSE corporate actions: {response.status_code} {response.text}"
                )
                return []

            data = response.json()

            # NSE API returns a list of dictionaries
            for item in data:
                # If instruments filter is provided, skip unmatched
                if instruments:
                    symbol = item.get("symbol", "")
                    if not any(inst.symbol == symbol for inst in instruments):
                        continue

                action = self._parse_nse_action(item)
                if action:
                    actions.append(action)

        except Exception as e:
            logger.error(f"Error fetching NSE corporate actions: {e}")

        return actions

    def _parse_nse_action(self, item: dict[str, Any]) -> CorporateAction | None:
        """Parse a single NSE JSON record into a CorporateAction."""
        try:
            subject = str(item.get("subject", "")).upper()
            purpose = str(item.get("purpose", "")).upper()

            action_type = None
            if "SPLIT" in subject or "SPLIT" in purpose or "SUB-DIVISION" in purpose:
                action_type = CorporateActionType.SPLIT
            elif "BONUS" in subject or "BONUS" in purpose:
                action_type = CorporateActionType.BONUS
            elif "DIVIDEND" in subject or "DIVIDEND" in purpose:
                action_type = CorporateActionType.DIVIDEND
            elif "RIGHTS" in subject or "RIGHTS" in purpose:
                action_type = CorporateActionType.RIGHTS
            else:
                return None

            # Date format in NSE API is typically 'DD-MMM-YYYY' e.g. '01-Jan-2024'
            ex_date_str = item.get("exDate")
            ex_date = None
            if ex_date_str and ex_date_str != "-":
                ex_date = datetime.strptime(ex_date_str, "%d-%b-%Y").replace(tzinfo=UTC).date()

            # Ratios are usually embedded in the purpose string,
            # e.g. "BONUS 1:2" or "FACE VALUE SPLIT FROM RS.10/- TO RS.2/-"
            # In a production system, this requires robust regex parsing.
            # We mock the extraction here based on common formats.
            num, den = self._extract_ratio(purpose, action_type)

            # NSE only provides 'symbol' (plus ISIN). The ID minted here is provisional:
            # the ingestion worker remaps it through the InstrumentResolver (ISIN first),
            # so a renamed symbol still lands on the instrument's permanent ID.
            symbol = item.get("symbol", "")

            # Deterministic ID: the same NSE record maps to the same row on every fetch.
            action_id = deterministic_action_id(
                self.PROVIDER_NAME,
                item.get("isin") or symbol,
                action_type.value,
                ex_date,
                num,
                den,
                item.get("ndStartDate"),
            )

            return CorporateAction(
                corporate_action_id=action_id,
                instrument_id=mint_instrument_id("NSE", symbol),
                isin=item.get("isin"),
                action_type=action_type,
                source=self.PROVIDER_NAME,
                created_at=datetime.now(UTC),
                ex_date=ex_date,
                ratio_numerator=num,
                ratio_denominator=den,
                source_record_id=item.get(
                    "ndStartDate"
                ),  # Using their internal date as a weak ID if present
            )
        except Exception as e:
            logger.warning(f"Could not parse NSE record {item}: {e}")
            return None

    def _extract_ratio(
        self, purpose: str, action_type: CorporateActionType
    ) -> tuple[float | None, float | None]:
        """Best-effort regex extraction of ratio from NSE purpose strings."""
        import re

        # e.g., "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
        if action_type == CorporateActionType.SPLIT:
            match = re.search(r"FROM RS\.?\s*(\d+(?:\.\d+)?).*TO RS\.?\s*(\d+(?:\.\d+)?)", purpose)
            if match:
                # Splitting from 10 to 2 means 1 share becomes 5.
                # In our convention N for D, it's 5 for 1, or numerator 5, denominator 1.
                old_fv = float(match.group(1))
                new_fv = float(match.group(2))
                if new_fv > 0:
                    return old_fv, new_fv

        # e.g., "Bonus 1:2"
        elif action_type == CorporateActionType.BONUS:
            match = re.search(r"(\d+)\s*:\s*(\d+)", purpose)
            if match:
                return float(match.group(1)), float(match.group(2))

        return None, None
