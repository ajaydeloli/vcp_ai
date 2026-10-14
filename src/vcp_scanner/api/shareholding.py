"""Shareholding pattern for the dashboard (F8 stage 3; FUNDAMENTALS_SPECIFICATION §15).

Display only. Point in time like the results: a filing counts from the day NSE broadcast it,
only quarter-end patterns form the series (event filings are left out), and of several
revisions of one quarter the latest broadcast by ``day`` is used. A quarter that was not
filed (or was set aside) makes its changes N/A, never 0. The pledge change is N/A across the
layout change of June 2025, because the old layouts count "pledged or encumbered" shares and
the new one pledges only.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from vcp_scanner.api import models as m
from vcp_scanner.api.queries import Cur
from vcp_scanner.data.providers._time import IST

#: quarters on the stock page
QUARTERS_SHOWN = 8
#: the latest quarter end is older than this (days) -> the next filing is overdue
STALE_DAYS = 120
#: the NSE close (IST)
MARKET_CLOSE = time(15, 30)
#: a pledge rise smaller than this (percentage points) is rounding, not a flag
PLEDGE_RISE_POINTS = 0.01

_SQL = """
SELECT instrument_id, as_on, available_at, revision_number, layout, promoter_pct,
       promoter_pledged_pct, fii_pct, fdi_pct, dii_pct, mf_pct, insurance_pct, government_pct,
       non_institutions_pct, public_pct, other_pct, shareholders
FROM shareholding_snapshots
WHERE is_quarter_end AND available_at <= ? AND as_on >= ? AND instrument_id IN (SELECT unnest(?))
QUALIFY row_number() OVER (PARTITION BY instrument_id, as_on
                           ORDER BY available_at DESC, revision_number DESC) = 1
ORDER BY instrument_id, as_on
"""


def _table_exists(cur: Cur) -> bool:
    row = cur.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'shareholding_snapshots'"
    ).fetchone()
    return bool(row and row[0])


def quarter_back(as_on: date, n: int) -> date:
    """The quarter end ``n`` quarters before the quarter end ``as_on``."""
    months = as_on.year * 12 + as_on.month - 1 - 3 * n
    year, month = divmod(months, 12)
    nxt = date(year + (month + 1) // 12, (month + 1) % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _cutoff(day: date) -> datetime:
    """As the results (§7): a filing counts at the close of ``day`` when NSE broadcast it by
    the 15:30 IST close; one broadcast in the evening counts from the next day."""
    return datetime.combine(day, MARKET_CLOSE, IST)


def _opt(x: Any) -> float | None:
    return None if x is None else float(x)


def _quarter(r: Any) -> m.ShareholdingQuarter:
    return m.ShareholdingQuarter(
        as_on=r[1], available_at=r[2].astimezone(IST), revision=int(r[3]), layout=str(r[4]),
        promoter_pct=float(r[5]), promoter_pledged_pct=_opt(r[6]), fii_pct=float(r[7]),
        fdi_pct=_opt(r[8]), dii_pct=float(r[9]), mf_pct=float(r[10]), insurance_pct=float(r[11]),
        government_pct=float(r[12]), non_institutions_pct=float(r[13]), public_pct=float(r[14]),
        other_pct=float(r[15]), shareholders=None if r[16] is None else int(r[16]),
    )  # fmt: skip


def _load(
    cur: Cur, ids: list[str], day: date, since: date,
) -> dict[str, list[m.ShareholdingQuarter]]:  # fmt: skip
    if not ids or not _table_exists(cur):
        return {}
    out: dict[str, list[m.ShareholdingQuarter]] = {}
    for r in cur.execute(_SQL, [_cutoff(day), since, ids]).fetchall():
        out.setdefault(str(r[0]), []).append(_quarter(r))
    return out


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 4)


def _change(
    item: str, now: m.ShareholdingQuarter, then: m.ShareholdingQuarter | None,
) -> float | None:  # fmt: skip
    return None if then is None else _diff(getattr(now, item), getattr(then, item))


def _same_pledge_basis(a: m.ShareholdingQuarter, b: m.ShareholdingQuarter) -> bool:
    return (a.layout == "NEW") == (b.layout == "NEW")


def summarize(quarters: list[m.ShareholdingQuarter], day: date) -> m.ShareholdingSummary | None:
    """The latest quarter (``quarters`` oldest first) with its changes over 1 and 4 quarters."""
    if not quarters:
        return None
    by_date = {q.as_on: q for q in quarters}
    cur_q = quarters[-1]
    q1 = by_date.get(quarter_back(cur_q.as_on, 1))
    q4 = by_date.get(quarter_back(cur_q.as_on, 4))
    pledge_1q = None
    if q1 is not None and _same_pledge_basis(cur_q, q1):
        pledge_1q = _diff(cur_q.promoter_pledged_pct, q1.promoter_pledged_pct)
    holders_1q = None
    if q1 is not None and cur_q.shareholders and q1.shareholders:
        holders_1q = round((cur_q.shareholders / q1.shareholders - 1) * 100, 2)
    return m.ShareholdingSummary(
        **cur_q.model_dump(),
        promoter_change_1q=_change("promoter_pct", cur_q, q1),
        promoter_change_4q=_change("promoter_pct", cur_q, q4),
        fii_change_1q=_change("fii_pct", cur_q, q1),
        fii_change_4q=_change("fii_pct", cur_q, q4),
        dii_change_1q=_change("dii_pct", cur_q, q1),
        dii_change_4q=_change("dii_pct", cur_q, q4),
        pledged_change_1q=pledge_1q,
        pledge_rising=pledge_1q is not None and pledge_1q >= PLEDGE_RISE_POINTS,
        shareholders_change_1q_pct=holders_1q,
        stale=(day - cur_q.as_on).days > STALE_DAYS,
    )


def shareholding_as_of(cur: Cur, ids: list[str], day: date) -> dict[str, m.ShareholdingSummary]:
    """instrument_id -> latest quarter as of the close of ``day`` (stocks without one absent)."""
    since = quarter_back(day, 7)
    out: dict[str, m.ShareholdingSummary] = {}
    for iid, qs in _load(cur, ids, day, since).items():
        s = summarize(qs, day)
        if s is not None:
            out[iid] = s
    return out


def stock_shareholding(
    cur: Cur, instrument_id: str, day: date,
) -> tuple[m.ShareholdingSummary | None, list[m.ShareholdingQuarter]]:  # fmt: skip
    """The summary and the last ``QUARTERS_SHOWN`` quarter ends (oldest first) of one stock."""
    since = quarter_back(day, QUARTERS_SHOWN + 6)
    qs = _load(cur, [instrument_id], day, since).get(instrument_id, [])
    return summarize(qs, day), qs[-QUARTERS_SHOWN:]
