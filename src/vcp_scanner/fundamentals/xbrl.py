"""Read the numbers of one NSE result filing (XBRL instance file) (F2).

Pure functions on bytes: no network, no database. Both NSE feeds use the same layout (verified
2026-10-10): contexts ``OneD`` (the reporting period, a quarter or a year), ``FourD`` (year to
date) and ``OneI`` (the balance-sheet date); facts carry plain rupee amounts (the "Crores" tag
is only how the filer displayed them), per-share amounts in rupees.

FUNDAMENTALS_SPECIFICATION §5.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

#: Profit and loss items: item name -> tags in order of preference (Ind-AS names first, then
#: the names banks use).
PNL_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromOperations",),
    "other_income": ("OtherIncome",),
    "finance_costs": ("FinanceCosts",),
    "depreciation": ("DepreciationDepletionAndAmortisationExpense",),
    "pbt": ("ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"),
    "net_profit": ("ProfitLossForPeriod", "ProfitLossForThePeriod"),
    "profit_owners": (
        "ProfitOrLossAttributableToOwnersOfParent",
        "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates",
    ),
    "eps": (
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "BasicEarningsPerShareAfterExtraordinaryItems",
        "BasicEarningsLossPerShareFromContinuingOperations",
        "BasicEarningsPerShareBeforeExtraordinaryItems",
    ),
}
#: Total income, the revenue of a financial company (no ``RevenueFromOperations``).
TOTAL_INCOME_TAG = "Income"
#: Balance-sheet items (context ``OneI``).
EQUITY_TAGS = ("EquityAttributableToOwnersOfParent", "Equity")
BORROWING_TAGS = ("BorrowingsCurrent", "BorrowingsNoncurrent")
#: Tag that only financial companies (banks) carry.
FINANCIAL_MARKER_TAGS = ("InterestEarned",)

QUARTER_DAYS = (75, 105)
ANNUAL_DAYS = (350, 380)


class FilingParseError(ValueError):
    """The file is not a usable result filing."""


@dataclass(frozen=True)
class PeriodFacts:
    start: date
    end: date
    items: dict[str, float]

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class ParsedFiling:
    basis: str | None  # CONSOLIDATED | STANDALONE
    currency: str | None
    period: PeriodFacts  # context OneD
    ytd: PeriodFacts | None  # context FourD, when different from OneD
    instant: dict[str, float] = field(default_factory=dict)  # balance sheet and ratios
    is_financial: bool = False

    @property
    def period_type(self) -> str | None:
        d = self.period.days
        if QUARTER_DAYS[0] <= d <= QUARTER_DAYS[1]:
            return "QUARTER"
        if ANNUAL_DAYS[0] <= d <= ANNUAL_DAYS[1]:
            return "ANNUAL"
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def _number(text: str | None) -> float | None:
    try:
        return float((text or "").strip().replace(",", ""))
    except ValueError:
        return None


def _basis(text: str | None) -> str | None:
    t = (text or "").strip().lower()
    if t == "consolidated":
        return "CONSOLIDATED"
    if t in ("standalone", "non-consolidated"):
        return "STANDALONE"
    return None


def parse_xbrl(data: bytes) -> ParsedFiling:
    """Parse a filing. Raises ``FilingParseError`` for anything unreadable or without a period."""
    if b"<!DOCTYPE" in data[:2000].upper() or b"<!ENTITY" in data[:5000].upper():
        raise FilingParseError("DTD or entity declarations are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FilingParseError(f"not XML: {exc}") from exc

    # Context -> (start, end) for duration contexts, from the context elements themselves.
    ctx_dates: dict[str, tuple[date | None, date | None]] = {}
    for el in root.iter():
        if _local(el.tag) == "context":
            start = end = None
            for child in el.iter():
                name = _local(child.tag)
                if name == "startDate":
                    start = _date(child.text)
                elif name in ("endDate", "instant"):
                    end = _date(child.text)
            ctx_dates[el.get("id", "")] = (start, end)

    # First fact per (tag, context); facts of other contexts (segments etc.) never match.
    facts: dict[tuple[str, str], str] = {}
    for el in root.iter():
        ref = el.get("contextRef")
        if ref is not None and el.text is not None:
            facts.setdefault((_local(el.tag), ref), el.text)

    def text(tag: str, ctx: str) -> str | None:
        return facts.get((tag, ctx))

    def items_of(ctx: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for item, tags in PNL_TAGS.items():
            for tag in tags:
                value = _number(text(tag, ctx))
                if value is not None:
                    out[item] = value
                    break
        return out

    def period_of(ctx: str) -> PeriodFacts | None:
        start = _date(text("DateOfStartOfReportingPeriod", ctx))
        end = _date(text("DateOfEndOfReportingPeriod", ctx))
        c_start, c_end = ctx_dates.get(ctx, (None, None))
        start, end = start or c_start, end or c_end
        if start is None or end is None or end < start:
            return None
        return PeriodFacts(start, end, items_of(ctx))

    period = period_of("OneD")
    if period is None:
        raise FilingParseError("no reporting period (context OneD)")
    ytd = period_of("FourD")
    if ytd is not None and (ytd.start, ytd.end) == (period.start, period.end) and period.items:
        ytd = None  # a first quarter or an annual statement: year to date equals the period

    is_financial = any(text(t, "OneD") is not None for t in FINANCIAL_MARKER_TAGS)
    if "revenue" not in period.items or is_financial:
        total = _number(text(TOTAL_INCOME_TAG, "OneD"))
        if total is not None and ("revenue" not in period.items or is_financial):
            period.items["revenue"] = total
            is_financial = True
            if ytd is not None:
                y = _number(text(TOTAL_INCOME_TAG, "FourD"))
                if y is not None:
                    ytd.items["revenue"] = y

    instant: dict[str, float] = {}
    for tag in EQUITY_TAGS:
        value = _number(text(tag, "OneI"))
        if value is not None:
            instant["equity"] = value
            break
    borrowings = [_number(text(t, "OneI")) for t in BORROWING_TAGS]
    if any(b is not None for b in borrowings):
        instant["borrowings"] = sum(b for b in borrowings if b is not None)
    ratio = _number(text("DebtEquityRatio", "OneD"))
    if ratio is not None:
        instant["debt_equity_ratio"] = ratio

    return ParsedFiling(
        basis=_basis(text("NatureOfReportStandaloneConsolidated", "OneD")),
        currency=(text("DescriptionOfPresentationCurrency", "OneD") or "").strip() or None,
        period=period,
        ytd=ytd,
        instant=instant,
        is_financial=is_financial,
    )
