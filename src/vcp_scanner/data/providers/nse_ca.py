"""NSE Corporate Actions Provider (DATA_SPECIFICATION §18A)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.data.identity import deterministic_action_id, mint_instrument_id
from vcp_scanner.domain.corporate_actions import CorporateAction
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import Instrument
from vcp_scanner.infrastructure.clock import Clock, utc_now

logger = logging.getLogger(__name__)

# Money amounts as NSE writes face values: "Rs 10/-", "Rs. 2/-", "Re 1/-", "INR 5".
_AMOUNT_RE = re.compile(r"(?:RS|RE|INR)\.?\s*(\d+(?:\.\d+)?)")
# "Bonus 1:2", "Bonus Issue 1 : 1", or the reverse spelling "1:1 Bonus".
_BONUS_AFTER_RE = re.compile(r"BONUS[^\d]*(\d+)\s*:\s*(\d+)")
_BONUS_BEFORE_RE = re.compile(r"(\d+)\s*:\s*(\d+)\s*BONUS")
# "Rights 1:14 @ Premium Rs 530/-", "Rights 1:19.07 @ Premium Rs 0", "Rights 21:20@ Premium ..."
# Also "Rights Issue 4:17@ Premium Rs 390/-" and "Rights 7:10 @ Prm Rs 102/-" (live, 2026-10-01).
_RIGHTS_RE = re.compile(r"RIGHTS(?:\s+ISSUE)?\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)")
_PREMIUM_RE = re.compile(r"(?:PREMIUM|PRM)\.?\s*(?:(?:RS|RE|INR)\.?)?\s*(\d+(?:\.\d+)?)")
# An Indian ISIN: "IN", a 1-char issuer type, 4-char issuer, 2-digit security type, 2-char issue
# number and a check digit (e.g. INE920A01029).
_ISIN_RE = re.compile(r"IN[A-Z0-9]{9}[0-9]")
# Rights in something other than equity shares ("RIGHTS - 7 CCPS AND 7 WARRANTS:40", QUINT
# 2026): not an equity rights issue, so no TERP factor applies.
_NON_EQUITY_RE = re.compile(r"\b(?:CCPS|WARRANTS?|DEBENTURES?|NCDS?|PREFERENCE)\b")
# Price-affecting events this provider does not model. Dropping them silently would leave the
# price series unadjusted with no trace, so they are reported instead (audit P1-2).
_UNHANDLED_MARKERS = (
    "CONSOLIDAT",
    "REDUCTION",
    "AMALGAMAT",
    "MERGER",
    "ARRANGEMENT",
    "DEBENTURE",  # a bonus of debentures pays value out without changing the share count
)


def parse_ratio(text: str, action_type: CorporateActionType) -> tuple[float, float] | None:
    """Extract ``(numerator, denominator)`` from NSE subject/purpose text, or ``None``.

    Conventions match ``AdjustmentEngine``:

    * SPLIT: ``(old face value, new face value)``. Rs 10 -> Re 1 gives ``(10, 1)``, i.e. one
      share becomes ten and prices are multiplied by ``1/10``.
    * BONUS: ``(bonus shares, shares held)``. ``Bonus 1:2`` gives ``(1, 2)``.
    * RIGHTS: ``(rights shares, shares held)``. ``Rights 1:14`` gives ``(1, 14)``.

    Returns ``None`` when the text does not contain a usable, positive ratio; callers must treat
    that as "ratio unknown", never as "no adjustment".
    """
    upper = text.upper().replace("\u20b9", "RS")
    if action_type == CorporateActionType.SPLIT:
        amounts = [float(a) for a in _AMOUNT_RE.findall(upper)]
        # Exactly two amounts (old, new). Three or more means the text mixes in something else
        # (a dividend amount, say) and guessing which two are face values is not safe.
        if len(amounts) != 2:
            return None
        old_fv, new_fv = amounts
        if old_fv <= 0 or new_fv <= 0 or old_fv == new_fv:
            return None
        return old_fv, new_fv
    if action_type in (CorporateActionType.BONUS, CorporateActionType.RIGHTS):
        if action_type == CorporateActionType.BONUS:
            m = _BONUS_AFTER_RE.search(upper) or _BONUS_BEFORE_RE.search(upper)
        else:
            m = _RIGHTS_RE.search(upper)
        if m is None:
            return None
        num, den = float(m.group(1)), float(m.group(2))
        if num <= 0 or den <= 0:
            return None
        return num, den
    return None


def rights_issue_price(text: str, face_value: object) -> float | None:
    """Rights issue price = face value + premium ("Rights 1:14 @ Premium Rs 530/-", face value 5
    -> 535; BHARTIARTL 2021). None when either part is missing: the factor is then unknown.
    """
    m = _PREMIUM_RE.search(text.upper().replace("\u20b9", "RS"))
    try:
        fv = float(str(face_value))
    except (TypeError, ValueError):
        return None
    if m is None or not fv > 0:
        return None
    return fv + float(m.group(1))


class NSECorporateActionProvider:
    """Fetches corporate actions from official NSE website."""

    PROVIDER_NAME = "NSE"
    BASE_URL = "https://www.nseindia.com"
    API_URL = "https://www.nseindia.com/api/corporates-corporateActions"
    #: NSE keeps main-board and SME (Emerge) actions in separate feeds. ``equities`` misses
    #: every SME bonus/split (series SM/ST); ``sme`` has them in the same record format
    #: (verified live 2026-10-01: 907 records 2021-01-01..2026-09-30).
    INDEXES: tuple[str, ...] = ("equities", "sme")

    def __init__(self, *, clock: Clock = utc_now) -> None:
        self._clock = clock
        # Filled by each ``get_actions`` call so the CLI can report what was not understood.
        self.unparsed_ratios: list[str] = []
        self.unhandled_records: list[str] = []
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
        self.unparsed_ratios = []
        self.unhandled_records = []

        seen: set[str] = set()
        for index in self.INDEXES:
            # Format dates as DD-MM-YYYY for NSE API
            params = {
                "index": index,
                "from_date": start.strftime("%d-%m-%Y"),
                "to_date": end.strftime("%d-%m-%Y"),
            }
            try:
                response = self._session.get(self.API_URL, params=params, timeout=15)

                if response.status_code != 200:
                    raise ProviderError(
                        f"NSE corporate actions request failed (index={index}): "
                        f"HTTP {response.status_code} {response.text[:200]}"
                    )

                data = response.json()
                if not isinstance(data, list):
                    raise ProviderError(
                        f"NSE corporate actions (index={index}): expected a list, "
                        f"got {type(data).__name__}"
                    )

                # NSE API returns a list of dictionaries
                for item in data:
                    # If instruments filter is provided, skip unmatched
                    if instruments:
                        symbol = item.get("symbol", "")
                        if not any(inst.symbol == symbol for inst in instruments):
                            continue

                    action = self._parse_nse_action(item)
                    # A stock that migrated from SME to the main board may be listed in both
                    # feeds; the deterministic ID keeps one copy.
                    if action and action.corporate_action_id not in seen:
                        seen.add(action.corporate_action_id)
                        actions.append(action)

            except ProviderError:
                raise
            except Exception as e:
                # An empty list would read as "no corporate actions", which leaves splits and
                # bonuses unadjusted. A failed fetch (of either feed) must never look like an
                # empty result.
                raise ProviderError(
                    f"NSE corporate actions fetch error (index={index}): {e}"
                ) from e

        return actions

    def _parse_nse_action(self, item: dict[str, Any]) -> CorporateAction | None:
        """Parse a single NSE JSON record into a CorporateAction."""
        try:
            subject = str(item.get("subject", "")).upper()
            purpose = str(item.get("purpose", "")).upper()

            text = f"{subject} {purpose}"
            action_type = None
            if "DEMERGER" in text:
                # Factor derived from the ex-date special pre-open price (audit step 2.4).
                action_type = CorporateActionType.DEMERGER
            elif "SPLIT" in text or "SUB-DIVISION" in text or "SUBDIVISION" in text:
                action_type = CorporateActionType.SPLIT
            elif "CONSOLIDAT" in text and "SHARE" in text:
                # A consolidation is a reverse split: "Consolidation Of Equity Shares From Re 1
                # Per Share To Rs 10 Per Share" (VERTOZ 2025) reads as SPLIT (1, 10), i.e. ten
                # shares become one and prices are multiplied by 10. A consolidation without
                # two face values stays visible as an unparsed ratio.
                action_type = CorporateActionType.SPLIT
            elif "BONUS" in text and "DEBENTURE" not in text:
                # "Scheme Of Arangement- Bonus - 1 Debenture For 1 Equity Share Held"
                # (BRITANNIA 2021) issues debentures, not shares: no share-count change, so it
                # is reported as unhandled below instead of becoming a ratio-less BONUS.
                action_type = CorporateActionType.BONUS
            elif "DIVIDEND" in text:
                action_type = CorporateActionType.DIVIDEND
            elif "RIGHTS" in text and not _NON_EQUITY_RE.search(text):
                action_type = CorporateActionType.RIGHTS
            elif "RIGHTS" in text or any(marker in text for marker in _UNHANDLED_MARKERS):
                # Price-affecting, but not modelled (capital reduction, merger, scheme, a bonus
                # of debentures, rights in CCPS/warrants/NCDs). Audit P1-10: stored as an
                # UNMODELLED action so the quality layer raises a warning event for it, instead
                # of only a log line (it used to be dropped).
                action_type = CorporateActionType.UNMODELLED
                described = f"{item.get('symbol', '?')}: {text.strip()}"
                self.unhandled_records.append(described)
                logger.warning("Unhandled price-affecting NSE action: %s", described)
            else:
                return None

            # Date format in NSE API is typically 'DD-MMM-YYYY' e.g. '01-Jan-2024'
            ex_date_str = item.get("exDate")
            ex_date = None
            if ex_date_str and ex_date_str != "-":
                ex_date = datetime.strptime(ex_date_str, "%d-%b-%Y").replace(tzinfo=UTC).date()

            # Ratios are embedded in the subject text, e.g. "Bonus 1:2" or
            # "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share".
            ratio = parse_ratio(text, action_type)
            num, den = ratio if ratio else (None, None)
            cash_amount = None
            if action_type == CorporateActionType.RIGHTS:
                cash_amount = rights_issue_price(text, item.get("faceVal"))
            if (
                ratio is None or (action_type == CorporateActionType.RIGHTS and cash_amount is None)
            ) and action_type in (
                CorporateActionType.SPLIT,
                CorporateActionType.BONUS,
                CorporateActionType.RIGHTS,
            ):
                # Kept (a split with an unknown ratio must stay visible to reconciliation) but
                # never silent: without a ratio the adjustment engine applies factor 1.0.
                described = f"{item.get('symbol', '?')}: {text.strip()}"
                self.unparsed_ratios.append(described)
                logger.warning(
                    "Could not read the ratio of NSE %s: %s", action_type.value, described
                )

            # NSE only provides 'symbol' (plus ISIN). The ID minted here is provisional:
            # the ingestion worker remaps it through the InstrumentResolver (ISIN first),
            # so a renamed symbol still lands on the instrument's permanent ID.
            symbol = item.get("symbol", "")
            # The SME feed's "isin" field holds an internal number ("341033" for KSOLVES),
            # not an ISIN. Passing it on would resolve nothing and store junk, so only a real
            # ISIN is kept; the resolver then falls back to the symbol.
            raw_isin = str(item.get("isin") or "").strip().upper()
            isin = raw_isin if _ISIN_RE.fullmatch(raw_isin) else None

            # Deterministic ID: the same NSE record maps to the same row on every fetch.
            action_id = deterministic_action_id(
                self.PROVIDER_NAME,
                isin or symbol,
                action_type.value,
                ex_date,
                num,
                den,
                item.get("ndStartDate"),
            )

            return CorporateAction(
                corporate_action_id=action_id,
                instrument_id=mint_instrument_id("NSE", symbol),
                isin=isin,
                action_type=action_type,
                source=self.PROVIDER_NAME,
                created_at=self._clock(),
                ex_date=ex_date,
                ratio_numerator=num,
                ratio_denominator=den,
                cash_amount=cash_amount,
                # Their internal date as a weak id; for an UNMODELLED action the record text,
                # so the warning can say what NSE listed.
                source_record_id=(
                    text.strip()[:300]
                    if action_type is CorporateActionType.UNMODELLED
                    else item.get("ndStartDate")
                ),
            )
        except Exception as e:
            logger.warning(f"Could not parse NSE record {item}: {e}")
            return None
