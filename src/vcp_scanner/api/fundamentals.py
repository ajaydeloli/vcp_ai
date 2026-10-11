"""Fundamentals for the dashboard (F5; FUNDAMENTALS_SPECIFICATION §11). Display only (§2):
nothing here feeds a scan, a score or the paper ledger.

The cross-section and the stock's summary read the stored views (``fundamental_metrics`` /
``fundamental_data_quality``, one row per stock and date its view changed): the view of a date D
is the row with the greatest ``as_of_date`` <= D, STALE when D is after ``stale_after``. The
stock's quarter table is computed from the stored snapshots by ``quarter_history``, the same
point-in-time rules that produced the stored rows.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from vcp_scanner.api import models as m
from vcp_scanner.api.context import Context
from vcp_scanner.api.queries import Cur, classification
from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
    load_share_actions,
    load_snapshots,
)
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE
from vcp_scanner.fundamentals.metrics import quarter_history

NO_DATA_REASON = "No results filed yet"
QUARTERS_SHOWN = 12

FUNDAMENTALS_SORTS = (
    "symbol", "eps_yoy", "eps_qoq", "sales_yoy", "eps_acceleration", "margin_expansion", "roe",
    "debt_to_equity", "availability", "period_end", "available_at", "rs_rank", "ttm_eps",
)  # fmt: skip

_VIEW_SQL = """
SELECT v.instrument_id, v.as_of_date, v.period_end, v.statement_basis, v.revenue, v.eps,
       v.ttm_eps, v.operating_margin, v.net_margin, v.revenue_qoq, v.eps_yoy, v.eps_qoq,
       v.revenue_yoy, v.eps_acceleration, v.margin_expansion, v.roe, v.debt_to_equity,
       v.statuses_json, d.availability_score, d.periods_available, d.stale_after, d.estimated,
       d.restated, d.fundamental_hard_gate_pass, d.fundamental_gate_reason, s.available_at
FROM fundamental_metrics v
JOIN fundamental_data_quality d USING (instrument_id, as_of_date)
LEFT JOIN fundamental_snapshots s ON s.fundamental_snapshot_id = v.fundamental_snapshot_id
WHERE v.as_of_date <= ? AND v.instrument_id IN (SELECT unnest(?))
QUALIFY row_number() OVER (PARTITION BY v.instrument_id ORDER BY v.as_of_date DESC) = 1
"""


def _tables_exist(cur: Cur) -> bool:
    row = cur.execute(
        "SELECT count(*) FROM information_schema.tables"
        " WHERE table_name IN ('fundamental_metrics', 'fundamental_data_quality')"
    ).fetchone()
    return bool(row and row[0] == 2)


def _num(x: Any) -> float | None:
    return None if x is None else float(x)


def views_as_of(cur: Cur, ids: list[str], day: date) -> dict[str, m.FundamentalSummary]:
    """instrument_id -> the stored view as of the close of ``day`` (stocks without one are
    absent). Empty before the tables exist."""
    if not ids or not _tables_exist(cur):
        return {}
    out: dict[str, m.FundamentalSummary] = {}
    for r in cur.execute(_VIEW_SQL, [day, ids]).fetchall():
        out[str(r[0])] = m.FundamentalSummary(
            view_date=r[1], period_end=r[2], basis=str(r[3]), revenue=_num(r[4]),
            eps=_num(r[5]), ttm_eps=_num(r[6]), operating_margin=_num(r[7]),
            net_margin=_num(r[8]), revenue_qoq=_num(r[9]), eps_yoy=_num(r[10]),
            eps_qoq=_num(r[11]), sales_yoy=_num(r[12]), eps_acceleration=_num(r[13]),
            margin_expansion=_num(r[14]), roe=_num(r[15]), debt_to_equity=_num(r[16]),
            statuses=json.loads(r[17]), availability=float(r[18]),
            quarters_available=int(r[19]), stale_after=r[20], stale=day > r[20],
            estimated=bool(r[21]), restated=bool(r[22]), gate_pass=bool(r[23]),
            gate_reason=r[24], available_at=None if r[25] is None else r[25].astimezone(IST),
        )  # fmt: skip
    return out


def stock_fundamentals(
    cur: Cur, instrument_id: str, symbol: str, company: str | None, day: date,
    data_time: datetime,
) -> m.StockFundamentalsResponse:  # fmt: skip
    summary = views_as_of(cur, [instrument_id], day).get(instrument_id)
    quarters: list[m.FundamentalQuarter] = []
    if summary is not None:
        rows = quarter_history(
            load_snapshots(cur, instrument_id), day, load_share_actions(cur, instrument_id),
            limit=QUARTERS_SHOWN,
        )  # fmt: skip
        quarters = [
            m.FundamentalQuarter(
                period_end=q.period_end, available_at=q.available_at.astimezone(IST), basis=q.basis,
                revision=q.revision, estimated=q.estimated, revenue=q.revenue, eps=q.eps,
                eps_as_filed=q.eps_as_filed, operating_margin=q.operating_margin,
                net_margin=q.net_margin, eps_yoy=q.eps_yoy.value,
                eps_yoy_status=q.eps_yoy.status, sales_yoy=q.sales_yoy.value,
                sales_yoy_status=q.sales_yoy.status,
            )
            for q in rows
        ]  # fmt: skip
    return m.StockFundamentalsResponse(
        as_of=day, data_time=data_time, symbol=symbol, company=company, summary=summary,
        quarters=quarters, reason=None if summary else NO_DATA_REASON,
    )  # fmt: skip


def _universe(cur: Cur, ctx: Context, day: date) -> list[tuple[str, str, str | None, Any]]:
    """(instrument id, symbol, company, RS rank) of every stock of the Trend Template scan on
    ``day``: the same universe the Screener shows."""
    rows = cur.execute(
        "SELECT t.instrument_id, coalesce(i.symbol, t.instrument_id), i.company_name, t.rs_rank"
        " FROM trend_template_results t LEFT JOIN instruments i USING (instrument_id)"
        " WHERE t.as_of_date = ? AND t.config_hash = ? AND t.data_snapshot_id = ?",
        [day, ctx.scan_hash, LIVE],
    ).fetchall()
    return [(str(r[0]), str(r[1]), r[2], r[3]) for r in rows]


def fundamentals_list(
    cur: Cur, ctx: Context, day: date, f: SimpleNamespace, data_time: datetime
) -> m.FundamentalsResponse:
    """One row per universe stock with its view as of ``day``, filtered, sorted and paged on the
    server. Stocks without results filed by ``day`` are not listed; ``universe`` against
    ``covered`` says how many."""
    stocks = _universe(cur, ctx, day)
    ids = [s[0] for s in stocks]
    views = views_as_of(cur, ids, day)
    sectors = classification(cur, ids)
    rows: list[m.FundamentalsRow] = []
    for iid, symbol, company, rs in stocks:
        v = views.get(iid)
        if v is None:
            continue
        rows.append(
            m.FundamentalsRow(
                **v.model_dump(), instrument_id=iid, symbol=symbol, company=company,
                sector=sectors.get(iid, (None, None))[0],
                rs_rank=None if rs is None else int(rs),
            )
        )  # fmt: skip
    needle = (f.q or "").strip().upper()

    def at_least(value: float | None, floor: float | None) -> bool:
        return floor is None or (value is not None and value >= floor)

    def keep(x: m.FundamentalsRow) -> bool:
        return all((
            not needle or needle in x.symbol.upper() or needle in (x.company or "").upper(),
            not f.sector or (x.sector or "").upper() == f.sector.upper(),
            at_least(x.eps_yoy, f.min_eps_yoy),
            at_least(x.sales_yoy, f.min_sales_yoy),
            at_least(x.availability, f.min_availability),
            not f.accelerating or (x.eps_acceleration is not None and x.eps_acceleration > 0),
            not f.current_only or not x.stale,
        ))  # fmt: skip

    kept = [x for x in rows if keep(x)]
    key = f.sort
    present = [x for x in kept if getattr(x, key) is not None]
    absent = sorted((x for x in kept if getattr(x, key) is None), key=lambda x: x.symbol)
    if key == "symbol":
        present.sort(key=lambda x: x.symbol, reverse=f.descending)
    else:
        present.sort(key=lambda x: (getattr(x, key), x.symbol), reverse=f.descending)
    ordered = present + absent  # a missing value sorts last either way, never as 0
    start = (f.page - 1) * f.page_size
    return m.FundamentalsResponse(
        as_of=day, data_time=data_time, universe=len(stocks), covered=len(rows),
        total=len(ordered), page=f.page, page_size=f.page_size,
        rows=ordered[start : start + f.page_size],
    )  # fmt: skip
