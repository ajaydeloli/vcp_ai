"""Read one NSE shareholding-pattern XBRL file (F8). Pure: bytes in, numbers out.

Three layouts, all verified 2026-10-13 on saved filings (FUNDAMENTALS_SPECIFICATION §15):

* **NEW** (quarters from June 2025; NSE's listing marks them ``newColumn: true``): contexts
  ``<Category>_ContextI``, percentages as fractions (``0.5048``), institutions split into
  domestic (``InstitutionsDomestic``) and foreign (FDI and FPI categories I and II). Pledge:
  ``EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares`` (pledges only).
* **SPLIT** (about 2022 to March 2025): contexts ``<Category>I``, percentages in percent, the
  same domestic / foreign split (the FPI contexts spelt ``...Catergory...``). Pledge:
  ``PledgedOrEncumberedSharesHeldAsPercentageOfTotalNumberOfShares`` (pledged *or otherwise
  encumbered*).
* **OLD** (to about 2022): contexts ``<Category>I``, one ``Institutions`` group: domestic
  institutions are that group less the foreign portfolio investors and foreign venture
  capital. Pledge as SPLIT.

The layout is read from the lines present, not from dates.

Every percentage returned is in percent of the company's shares, except the pledge, which is in
percent of the promoter group's own shares. A category the filing does not show is 0 (no such
holders); a filing without the promoter, public or total lines is not usable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

FORMAT_NEW = "NEW"
FORMAT_SPLIT = "SPLIT"
FORMAT_OLD = "OLD"
#: promoter + public + other must make 100 % within this (rounding of two-decimal percentages)
TOTAL_TOLERANCE = 0.6

_PCT = "shareholdingasapercentageoftotalnumberofshares"
_PROMOTER = "shareholdingofpromoterandpromotergroup"


class ShareholdingParseError(ValueError):
    """The file is not a usable shareholding-pattern filing."""


@dataclass(frozen=True)
class Shareholding:
    report_date: date | None
    symbol: str | None
    layout: str  # NEW | SPLIT | OLD
    promoter_pct: float
    promoter_pledged_pct: float | None  # % of promoter shares; None when the filing says nothing
    fii_pct: float  # foreign portfolio investors (FPI categories I and II)
    fdi_pct: float | None  # foreign direct investment (new layout only)
    dii_pct: float  # domestic institutions
    mf_pct: float  # mutual funds (part of DII)
    insurance_pct: float  # insurance companies (part of DII)
    government_pct: float
    non_institutions_pct: float  # retail, HNIs, bodies corporate, NRIs and other non-institutions
    public_pct: float  # institutions + government + non-institutions
    other_pct: float  # neither promoter nor public (employee trusts, depository receipts)
    shareholders: int | None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _context_key(context_id: str) -> str:
    """'MutualFundsOrUTI_ContextI' and 'MutualFundsOrUtiI' -> 'mutualfundsoruti'."""
    c = context_id.strip()
    if c.endswith("_ContextI"):
        c = c[: -len("_ContextI")]
    elif c.endswith("I"):
        c = c[:-1]
    return c.lower()


def _num(text: str | None) -> float | None:
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_shareholding(data: bytes) -> Shareholding:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ShareholdingParseError(f"not XML: {exc}") from exc
    facts: dict[tuple[str, str], str] = {}
    new_layout = False
    for el in root:
        ctx = el.get("contextRef")
        if ctx is None or el.text is None:
            continue
        new_layout = new_layout or ctx.endswith("_ContextI")
        facts[(_local(el.tag).lower(), _context_key(ctx))] = el.text.strip()

    def pct(key: str) -> float | None:
        return _num(facts.get((_PCT, key)))

    total = pct("shareholdingpattern")
    promoter = pct(_PROMOTER)
    public = pct("publicshareholding")
    npnp = "sharesheldbynonpromoternonpublicshareholders"
    if public is None:
        raise ShareholdingParseError("no promoter, public or total shareholding line")
    if promoter is None:
        # companies without a promoter group (most banks, ITC, L&T, many new listings) file
        # no promoter line at all: their promoter holding is 0, not unknown
        promoter = 0.0
    # Units: fractions (new layout) or percent (older ones). Read from the parts, which must
    # add up, rather than from the total line, which some filers get wrong (BSE 2022: 11.68).
    parts = promoter + public + (pct(npnp) or 0.0)
    if abs(parts - 1.0) < 0.02 or (total is not None and abs(total - 1.0) < 0.02 and parts < 1.5):
        scale = 100.0
    elif abs(parts - 100.0) < 2.0 or (total is not None and abs(total - 100.0) < 2.0):
        scale = 1.0
    else:
        raise ShareholdingParseError(
            f"promoter {promoter} + public {public} is neither 100 % nor 1 (total {total})"
        )

    def p(key: str) -> float:
        v = pct(key)
        return 0.0 if v is None else v * scale

    promoter_pct, public_pct = promoter * scale, public * scale
    # Neither promoter nor public (employee trusts, depository receipts). Filers differ: some
    # leave these shares out of the base (promoter + public = 100, e.g. RELIANCE), others count
    # them (promoter + public + these = 100, e.g. JSWSTEEL). "other" is what is left to 100 %,
    # allowed beyond rounding only when the filing has such holders.
    other_pct = max(0.0, 100.0 - promoter_pct - public_pct)
    has_npnp = bool(_num(facts.get(("numberofshares", npnp)))) or bool(pct(npnp))
    if promoter_pct + public_pct > 100.0 + TOTAL_TOLERANCE or (
        other_pct > TOTAL_TOLERANCE and not has_npnp
    ):
        raise ShareholdingParseError(
            f"promoter {promoter_pct:.2f} % + public {public_pct:.2f} % is not 100 %"
        )
    if other_pct <= TOTAL_TOLERANCE and not has_npnp:
        other_pct = 0.0  # rounding

    fdi: float | None
    if pct("institutionsdomestic") is not None or pct("institutionsforeign") is not None:
        layout = FORMAT_NEW if new_layout else FORMAT_SPLIT
        fii = sum(p(f"institutionsforeignportfolioinvestor{spelt}{n}")
                  for spelt in ("category", "catergory") for n in ("one", "two"))  # fmt: skip
        fdi = p("foreigndirectinvestment")
        dii = p("institutionsdomestic")
    else:
        layout = FORMAT_OLD
        fii = p("institutionsforeignportfolioinvestor")
        fdi = None
        dii = max(0.0, p("institutions") - fii - p("foreignventurecapitalinvestors"))
    government = p("governments") or p("goverments")  # NSE's older taxonomy spells it so

    pledged_pct: float | None
    new_pledge = _num(
        facts.get(("encumberedshareunderpledgedaspercentageoftotalnumberofshares", _PROMOTER))
    )
    old_pledge = _num(
        facts.get(("pledgedorencumberedsharesheldaspercentageoftotalnumberofshares", _PROMOTER))
    )
    flag = facts.get(("whetheranysharesheldbypromotersareencumberedunderpledged", "main"))
    if new_pledge is not None:
        pledged_pct = new_pledge * scale
    elif old_pledge is not None:
        pledged_pct = old_pledge * scale
    else:
        pledged_pct = 0.0 if flag == "false" else None

    holders = _num(facts.get(("numberofshareholders", "shareholdingpattern")))
    report = next((facts[("dateofreport", c)] for c in ("main", "one", "maind", "oned")
                   if ("dateofreport", c) in facts), None)  # fmt: skip
    symbol = next((facts[("symbol", c)] for c in ("main", "one", "maind", "oned")
                   if ("symbol", c) in facts), None)  # fmt: skip
    try:
        report_date = date.fromisoformat(report) if report else None
    except ValueError:
        report_date = None
    return Shareholding(
        report_date=report_date, symbol=symbol, layout=layout,
        promoter_pct=promoter_pct, promoter_pledged_pct=pledged_pct,
        fii_pct=fii, fdi_pct=fdi, dii_pct=dii,
        mf_pct=p("mutualfundsoruti"), insurance_pct=p("insurancecompanies"),
        government_pct=government, non_institutions_pct=p("noninstitutions"),
        public_pct=public_pct, other_pct=other_pct,
        shareholders=None if holders is None else int(holders),
    )  # fmt: skip
