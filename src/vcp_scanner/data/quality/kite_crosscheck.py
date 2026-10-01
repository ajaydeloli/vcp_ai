"""Cross-check our adjusted series against Kite's history (audit step 2.5).

Kite serves history adjusted as of the fetch time for a wider set of actions than we apply
(large dividends; demergers by the company's cost-of-acquisition split). With NSE bhavcopy as
the raw source, Kite is a check, not an input. For each common trading day the ratio

    r(t) = Kite close(t) / our adjusted close(t)

is flat while both sides agree. A step between consecutive days t-1 and t means one side
rescaled the history before t, i.e. an action with ex-date in (t-1, t]. Each step is attributed
to our reconciled actions in that window:

* DIVIDEND -> INFO ``KITE_DIVIDEND``: Kite adjusts for it, we keep raw prices (by design).
* DEMERGER -> INFO ``DEMERGER_METHOD``: we use NSE's ex-date price discovery, Kite the
  company's cost-of-acquisition split (RELIANCE 2023: 0.9079 vs about 0.9532).
* SPLIT / BONUS / RIGHTS -> WARNING ``FACTOR_MISMATCH``: both apply it, with different factors.
* nothing -> WARNING ``UNEXPLAINED``: an action one side knows and the other does not.

A step that reverses on the next day is one bad bar (WARNING ``BAR_MISMATCH``), and a ratio
away from 1 on the latest common day is WARNING ``LEVEL_MISMATCH`` (recent bars should be
identical: no adjustment applies after the last ex-date). Read-only; nothing is stored.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from vcp_scanner.domain.corporate_actions import CorporateActionResolution
from vcp_scanner.domain.enums import CorporateActionType

#: Default relative tolerance. Kite rounds adjusted prices to 2 decimals (0.1 % of a Rs 5
#: price), so smaller steps are noise; a typical large dividend is 1-3 %.
DEFAULT_TOLERANCE = 0.002


class CrosscheckKind(StrEnum):
    KITE_DIVIDEND = "KITE_DIVIDEND"
    DEMERGER_METHOD = "DEMERGER_METHOD"
    FACTOR_MISMATCH = "FACTOR_MISMATCH"
    UNEXPLAINED = "UNEXPLAINED"
    BAR_MISMATCH = "BAR_MISMATCH"
    LEVEL_MISMATCH = "LEVEL_MISMATCH"


_INFO_KINDS = frozenset({CrosscheckKind.KITE_DIVIDEND, CrosscheckKind.DEMERGER_METHOD})


@dataclass(frozen=True, slots=True)
class CrosscheckFinding:
    trade_date: date  # the day the ratio changed (ex-date window end)
    kind: CrosscheckKind
    step: float  # r(t-1) / r(t): the factor Kite applied relative to us before this day
    previous_date: date | None = None
    actions: tuple[str, ...] = ()

    @property
    def severity(self) -> str:
        return "INFO" if self.kind in _INFO_KINDS else "WARNING"


@dataclass
class CrosscheckReport:
    common_days: int = 0
    only_ours: int = 0
    only_kite: int = 0
    findings: list[CrosscheckFinding] = field(default_factory=list)

    @property
    def warnings(self) -> list[CrosscheckFinding]:
        return [f for f in self.findings if f.severity == "WARNING"]


def _attribute(
    resolutions: Sequence[CorporateActionResolution], after: date, through: date
) -> tuple[CrosscheckKind, tuple[str, ...]]:
    types = sorted(
        {
            r.action_type
            for r in resolutions
            if r.ex_date is not None and after < r.ex_date <= through
        },
        key=lambda t: t.value,
    )
    names = tuple(t.value for t in types)
    scaling = {CorporateActionType.SPLIT, CorporateActionType.BONUS, CorporateActionType.RIGHTS}
    if any(t in scaling for t in types):
        return CrosscheckKind.FACTOR_MISMATCH, names
    if CorporateActionType.DEMERGER in types:
        return CrosscheckKind.DEMERGER_METHOD, names
    if CorporateActionType.DIVIDEND in types:
        return CrosscheckKind.KITE_DIVIDEND, names
    return CrosscheckKind.UNEXPLAINED, names


def crosscheck(
    ours: Sequence[tuple[date, float]],
    kite: Sequence[tuple[date, float]],
    resolutions: Sequence[CorporateActionResolution],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> CrosscheckReport:
    """Compare two close series (ours adjusted, Kite's) and explain every step in their ratio."""
    our_close = {d: c for d, c in ours if c > 0 and math.isfinite(c)}
    kite_close = {d: c for d, c in kite if c > 0 and math.isfinite(c)}
    days = sorted(our_close.keys() & kite_close.keys())
    report = CrosscheckReport(
        common_days=len(days),
        only_ours=len(our_close.keys() - kite_close.keys()),
        only_kite=len(kite_close.keys() - our_close.keys()),
    )
    if not days:
        return report
    limit = math.log1p(tolerance)
    log_r = [math.log(kite_close[d] / our_close[d]) for d in days]

    i = 1
    while i < len(days):
        jump = log_r[i - 1] - log_r[i]
        if abs(jump) <= limit:
            i += 1
            continue
        reverses = i + 1 < len(days) and abs(jump + (log_r[i] - log_r[i + 1])) <= limit
        if reverses:
            report.findings.append(
                CrosscheckFinding(
                    days[i], CrosscheckKind.BAR_MISMATCH, math.exp(-jump), days[i - 1]
                )
            )
            i += 2
            continue
        kind, names = _attribute(resolutions, days[i - 1], days[i])
        report.findings.append(CrosscheckFinding(days[i], kind, math.exp(jump), days[i - 1], names))
        i += 1

    if abs(log_r[-1]) > limit:
        report.findings.append(
            CrosscheckFinding(days[-1], CrosscheckKind.LEVEL_MISMATCH, math.exp(log_r[-1]))
        )
    return report
