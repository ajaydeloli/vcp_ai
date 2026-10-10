"""Fundamental metrics as of a date (F3; FUNDAMENTALS_SPECIFICATION §6-8).

Everything here is a pure function of the stored snapshots, the share-count actions of the stock
and the as-of date: the same inputs always give the same view, and filings broadcast after the
cutoff are invisible. Nothing here is read by the scan, the score or the backtest (spec §2).

Point in time (§7): a snapshot is usable for ``as_of`` only if it was broadcast at or before
15:30 IST on ``as_of`` (the close), was not INVALID, and is the highest revision of its period
available then.

EPS comparability (§5.3): a bonus or split changes the share count, and Ind-AS 33 restates EPS
for one that happens before the results are approved. So an earlier EPS is put on the later
filing's share basis by the SPLIT/BONUS factors with an ex-date after the earlier filing's
broadcast date and on or before the later one's.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CLOSE = time(15, 30)

# Statuses (§8, PROJECT_DESIGN §32).
AVAILABLE = "AVAILABLE"
MISSING = "MISSING"
NEGATIVE_BASE = "NEGATIVE_BASE"
BASIS_CHANGE = "BASIS_CHANGE"
NOT_APPLICABLE = "NOT_APPLICABLE"

SCORE_METRICS = (
    "eps_yoy",
    "eps_qoq",
    "sales_yoy",
    "eps_acceleration",
    "margin_expansion",
    "roe",
    "debt_to_equity",
)


@dataclass(frozen=True)
class SnapshotData:
    """One stored snapshot with its facts, as the metric functions need it."""

    snapshot_id: str
    period_end: date
    period_type: str  # QUARTER | ANNUAL
    basis: str  # CONSOLIDATED | STANDALONE
    revision: int
    available_at: datetime
    data_status: str  # OK | ESTIMATED | INVALID
    facts: dict[str, dict[str, float]]  # scope -> item -> value

    def quarter(self, item: str) -> float | None:
        return self.facts.get("QUARTER", {}).get(item)

    def instant(self, item: str) -> float | None:
        return self.facts.get("INSTANT", {}).get(item)

    @property
    def is_financial(self) -> bool:
        return self.instant("is_financial") == 1.0


@dataclass(frozen=True)
class ShareAction:
    """A split or bonus: ``factor`` is the price factor (0.5 for a 1:1 bonus)."""

    ex_date: date
    factor: float


@dataclass(frozen=True)
class Metric:
    value: float | None
    status: str


@dataclass
class FundamentalView:
    as_of: date
    snapshot_id: str | None = None  # the latest usable quarter
    period_end: date | None = None
    basis: str | None = None
    available_at: datetime | None = None
    revenue: float | None = None
    eps: float | None = None
    operating_margin: float | None = None
    ttm_eps: float | None = None
    quarters_available: int = 0
    metrics: dict[str, Metric] = field(default_factory=dict)
    availability_score: float = 0.0
    stale: bool = False
    estimated: bool = False  # the latest quarter was derived from year-to-date values
    restated: bool = False  # the latest quarter is a revision > 0
    hard_gate_pass: bool = False
    gate_reason: str | None = None

    def value(self, name: str) -> float | None:
        m = self.metrics.get(name)
        return m.value if m else None


# --- helpers ----------------------------------------------------------------------------------


def cutoff_for(as_of: date) -> datetime:
    """15:30 IST on ``as_of``: a filing broadcast later is first used the next day."""
    return datetime.combine(as_of, CLOSE, tzinfo=IST)


def month_end_shift(end: date, months: int) -> date:
    """The last day of the month ``months`` after ``end``'s month (negative goes back)."""
    index = end.year * 12 + end.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, calendar.monthrange(year, month + 1)[1])


def usable(snapshots: Iterable[SnapshotData], cutoff: datetime) -> list[SnapshotData]:
    """Point-in-time selection: highest revision per period available at ``cutoff``."""
    best: dict[tuple[date, str, str], SnapshotData] = {}
    for s in snapshots:
        if s.available_at > cutoff or s.data_status == "INVALID":
            continue
        key = (s.period_end, s.period_type, s.basis)
        if key not in best or s.revision > best[key].revision:
            best[key] = s
    return sorted(best.values(), key=lambda s: (s.period_end, s.period_type, s.basis))


def share_factor(actions: Sequence[ShareAction], earlier: datetime, later: datetime) -> float:
    """Multiply an EPS filed at ``earlier`` by this to put it on the share basis of ``later``."""
    factor = 1.0
    for a in actions:
        if earlier.astimezone(IST).date() < a.ex_date <= later.astimezone(IST).date():
            factor *= a.factor
    return factor


def _pct_change(now: float | None, before: float | None) -> Metric:
    if now is None or before is None:
        return Metric(None, MISSING)
    if before <= 0:
        return Metric(None, NEGATIVE_BASE)
    return Metric((now - before) / abs(before) * 100.0, AVAILABLE)


def operating_margin(s: SnapshotData) -> Metric:
    """(PBT + finance costs + depreciation - other income) / revenue, % (§5.4)."""
    if s.is_financial:
        return Metric(None, NOT_APPLICABLE)
    parts = [s.quarter(k) for k in ("pbt", "finance_costs", "depreciation", "other_income")]
    revenue = s.quarter("revenue")
    if any(p is None for p in parts) or revenue is None or revenue <= 0:
        return Metric(None, MISSING)
    pbt, finance, depreciation, other = (p or 0.0 for p in parts)
    return Metric((pbt + finance + depreciation - other) / revenue * 100.0, AVAILABLE)


# --- the view -----------------------------------------------------------------------------------


def compute_view(
    snapshots: Iterable[SnapshotData],
    as_of: date,
    actions: Sequence[ShareAction] = (),
    *,
    staleness_days: int = 120,
    min_availability: float = 0.5,
) -> FundamentalView:
    """All metrics of one stock as known at the close of ``as_of``."""
    view = FundamentalView(as_of=as_of)
    snaps = usable(snapshots, cutoff_for(as_of))
    quarters = [s for s in snaps if s.period_type == "QUARTER"]
    if not quarters:
        view.metrics = {m: Metric(None, MISSING) for m in SCORE_METRICS}
        view.gate_reason = "NO_DATA"
        return view

    # Basis policy prefer_consolidated, decided by the latest quarter.
    latest_end = max(s.period_end for s in quarters)
    at_latest = {s.basis: s for s in quarters if s.period_end == latest_end}
    basis = "CONSOLIDATED" if "CONSOLIDATED" in at_latest else "STANDALONE"
    latest = at_latest[basis]
    series = {s.period_end: s for s in quarters if s.basis == basis}
    other_series = {s.period_end for s in quarters if s.basis != basis}

    def find(end: date) -> SnapshotData | None:
        return series.get(end)

    def missing_status(end: date) -> str:
        return BASIS_CHANGE if end in other_series else MISSING

    def eps_on_latest_basis(s: SnapshotData, ref: SnapshotData) -> float | None:
        eps = s.quarter("eps")
        return (
            None if eps is None else eps * share_factor(actions, s.available_at, ref.available_at)
        )

    def eps_growth(now: SnapshotData, months_back: int) -> Metric:
        then_end = month_end_shift(now.period_end, months_back)
        then = find(then_end)
        if then is None:
            return Metric(None, missing_status(then_end))
        return _pct_change(now.quarter("eps"), eps_on_latest_basis(then, now))

    view.snapshot_id = latest.snapshot_id
    view.period_end = latest.period_end
    view.basis = basis
    view.available_at = latest.available_at
    view.revenue = latest.quarter("revenue")
    view.eps = latest.quarter("eps")
    view.estimated = latest.data_status == "ESTIMATED"
    view.restated = latest.revision > 0
    view.quarters_available = len(series)

    m: dict[str, Metric] = {}
    m["eps_yoy"] = eps_growth(latest, -12)
    m["eps_qoq"] = eps_growth(latest, -3)

    year_ago_end = month_end_shift(latest_end, -12)
    year_ago = find(year_ago_end)
    m["sales_yoy"] = (
        _pct_change(latest.quarter("revenue"), year_ago.quarter("revenue"))
        if year_ago
        else Metric(None, missing_status(year_ago_end))
    )

    prev = find(month_end_shift(latest_end, -3))
    prev_yoy = eps_growth(prev, -12) if prev else Metric(None, MISSING)
    if m["eps_yoy"].value is not None and prev_yoy.value is not None:
        m["eps_acceleration"] = Metric(m["eps_yoy"].value - prev_yoy.value, AVAILABLE)
    else:
        m["eps_acceleration"] = Metric(None, MISSING)

    om = operating_margin(latest)
    view.operating_margin = om.value
    if om.status == NOT_APPLICABLE:
        m["margin_expansion"] = om
    elif year_ago is None:
        m["margin_expansion"] = Metric(None, missing_status(year_ago_end))
    else:
        om_then = operating_margin(year_ago)
        m["margin_expansion"] = (
            Metric(om.value - om_then.value, AVAILABLE)
            if om.value is not None and om_then.value is not None
            else Metric(None, MISSING)
        )

    # Trailing four quarters, all on the latest share basis.
    last4 = [find(month_end_shift(latest_end, -3 * i)) for i in range(4)]
    if all(s is not None for s in last4):
        four = [s for s in last4 if s is not None]
        eps4 = [eps_on_latest_basis(s, latest) for s in four]
        view.ttm_eps = sum(e for e in eps4 if e is not None) if None not in eps4 else None
        profits = [s.quarter("profit_owners") or s.quarter("net_profit") for s in four]
    else:
        profits = []

    # Balance sheet: the latest two dates on or before the latest quarter, same basis, any type.
    sheets: dict[date, float] = {}
    for s in snaps:
        equity = s.instant("equity")
        if s.basis == basis and s.period_end <= latest_end and equity is not None:
            sheets[s.period_end] = equity
    sheet_dates = sorted(sheets)[-2:]
    if profits and None not in profits and sheet_dates:
        avg_equity = sum(sheets[d] for d in sheet_dates) / len(sheet_dates)
        m["roe"] = (
            Metric(sum(p for p in profits if p is not None) / avg_equity * 100.0, AVAILABLE)
            if avg_equity > 0
            else Metric(None, NEGATIVE_BASE)
        )
    else:
        m["roe"] = Metric(None, MISSING)

    m["debt_to_equity"] = _debt_to_equity(snaps, basis, latest_end)

    view.metrics = m
    available = sum(1 for k in SCORE_METRICS if m[k].value is not None)
    view.availability_score = available / len(SCORE_METRICS)
    view.stale = (as_of - latest_end).days > staleness_days

    reasons = []
    if view.availability_score < min_availability:
        reasons.append("INSUFFICIENT_DATA")
    if view.ttm_eps is not None and view.ttm_eps < 0:
        reasons.append("NEGATIVE_TTM_EPS")
    view.hard_gate_pass = not reasons
    view.gate_reason = ";".join(reasons) or None
    return view


def _debt_to_equity(snaps: list[SnapshotData], basis: str, latest_end: date) -> Metric:
    """Borrowings / equity from the latest balance sheet; else the filed ratio.

    The balance sheet comes first because some filings carry a filed ratio of 0 next to real
    borrowings (RELIANCE 2024-03-31: DebtEquityRatio 0, borrowings Rs 3.2 lakh crore).
    """
    candidates = [s for s in snaps if s.basis == basis and s.period_end <= latest_end]
    for s in sorted(candidates, key=lambda s: s.period_end, reverse=True):
        equity, debt = s.instant("equity"), s.instant("borrowings")
        if equity is not None and debt is not None:
            return Metric(debt / equity, AVAILABLE) if equity > 0 else Metric(None, NEGATIVE_BASE)
    for s in sorted(candidates, key=lambda s: s.period_end, reverse=True):
        ratio = s.instant("debt_equity_ratio")
        if ratio is not None:
            return Metric(ratio, AVAILABLE)
    return Metric(None, MISSING)
