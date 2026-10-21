"""The Trend Template page (owner, 2026-10-21): the whole scan through the ten conditions of
TREND_TEMPLATE_SPECIFICATION section 2. Read only, from the stored scan results: which condition
keeps most stocks out, who started or stopped passing, who fails exactly one condition, and the
weekly stage by sector."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from vcp_scanner.api import models as m
from vcp_scanner.api.context import Context
from vcp_scanner.api.queries import Cur, _f, classification
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE

LIST_LIMIT = 60
WEEK_SCANS = 5  # stage moves are measured over the last five scan days
ENOUGH = ("PASS", "FAIL")  # statuses with all conditions evaluated


@dataclass
class _Stock:
    status: str
    passed: bool
    stage: str | None
    rs_rank: int | None
    fails: list[m.TTFail] = field(default_factory=list)


def scan_days(cur: Cur, ctx: Context, on: date | None) -> list[date]:
    """Scan days of the current configuration, newest first, up to ``on``."""
    rows = cur.execute(
        "SELECT DISTINCT as_of_date FROM trend_template_results WHERE config_hash = ?"
        " AND data_snapshot_id = ? AND (? IS NULL OR as_of_date <= ?) ORDER BY 1 DESC LIMIT ?",
        [ctx.scan_hash, LIVE, on, on, WEEK_SCANS + 1],
    ).fetchall()
    return [r[0] for r in rows]


def _results(cur: Cur, ctx: Context, day: date) -> dict[str, _Stock]:
    out = {
        str(r[0]): _Stock(str(r[1]), bool(r[2]), None if r[3] is None else str(r[3]),
                          None if r[4] is None else int(r[4]))
        for r in cur.execute(
            "SELECT instrument_id, status, coalesce(trend_template_pass, false), weekly_stage,"
            " rs_rank FROM trend_template_results WHERE as_of_date = ? AND config_hash = ?"
            " AND data_snapshot_id = ?",
            [day, ctx.scan_hash, LIVE],
        ).fetchall()
    }  # fmt: skip
    return out


def _conditions(
    cur: Cur, ctx: Context, day: date
) -> list[tuple[str, int, str, bool, float | None, float | None]]:  # noqa: E501
    return [
        (str(r[0]), int(r[1]), str(r[2]), bool(r[3]), _f(r[4]), _f(r[5]))
        for r in cur.execute(
            "SELECT instrument_id, condition_id, condition_name, coalesce(passed, false),"
            " measurement, threshold FROM trend_template_conditions WHERE as_of_date = ?"
            " AND config_hash = ? AND data_snapshot_id = ?",
            [day, ctx.scan_hash, LIVE],
        ).fetchall()
    ]


def trend_template(
    cur: Cur, ctx: Context, day: date, data_time: datetime
) -> m.TrendTemplateResponse:
    days = scan_days(cur, ctx, day)
    previous = days[1] if len(days) > 1 else None
    week_ago = days[-1] if len(days) > 1 else None
    today = _results(cur, ctx, day)
    before = _results(cur, ctx, previous) if previous else {}
    week = _results(cur, ctx, week_ago) if week_ago else {}

    passed_by: dict[int, int] = defaultdict(int)
    names: dict[int, str] = {}
    for iid, cid, name, ok, meas, thr in _conditions(cur, ctx, day):
        s = today.get(iid)
        if s is None or s.status not in ENOUGH:
            continue
        names[cid] = name
        if ok:
            passed_by[cid] += 1
        else:
            s.fails.append(m.TTFail(name=name, measurement=meas, threshold=thr))
    only_fail: dict[str, int] = defaultdict(int)
    for s in today.values():
        if s.status in ENOUGH and len(s.fails) == 1:
            only_fail[s.fails[0].name] += 1
    conditions = [
        m.TTConditionCount(condition_id=cid, name=names[cid], passed=passed_by[cid],
                           only_fail=only_fail[names[cid]])
        for cid in sorted(names)
    ]  # fmt: skip

    population = [iid for iid, s in today.items() if s.status in ENOUGH]
    new = [i for i in population if today[i].passed and previous and not before.get(i, _NO).passed]
    dropped = [i for i, s in before.items() if s.passed and not today.get(i, _NO).passed]
    nearly = sorted((i for i in population if len(today[i].fails) == 1),
                    key=lambda i: -(today[i].rs_rank or 0))  # fmt: skip
    moved = [
        i for i in population
        if today[i].stage == "STAGE_2" and week.get(i) is not None
        and week[i].stage not in (None, "STAGE_2", "INSUFFICIENT_DATA")
    ]  # fmt: skip

    shown = set(new) | set(dropped) | set(nearly[:LIST_LIMIT]) | set(moved)
    info = _info(cur, sorted(shown | set(population)), day)

    def stock(iid: str, prior: str | None = None) -> m.TTStock:
        s = today.get(iid, _NO)
        sym, company, sector, close = info.get(iid, (iid.split("|")[-1], None, None, None))
        return m.TTStock(symbol=sym, company=company, sector=sector, stage=s.stage,
                         rs_rank=s.rs_rank, close=close, failed=s.fails,
                         previous_stage=prior)  # fmt: skip

    def by_rs(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda i: -(today.get(i, _NO).rs_rank or 0))

    return m.TrendTemplateResponse(
        as_of=day, data_time=data_time, previous=previous, week_ago=week_ago,
        population=len(population), passing=sum(1 for i in population if today[i].passed),
        passing_previous=(sum(1 for s in before.values() if s.passed) if previous else None),
        conditions=conditions,
        new_passers=[stock(i) for i in by_rs(new)],
        dropped=[stock(i) for i in by_rs(dropped)],
        nearly=[stock(i) for i in nearly[:LIST_LIMIT]], nearly_total=len(nearly),
        stages=_stages(today, population, info),
        into_stage2=[stock(i, week[i].stage) for i in by_rs(moved)[:LIST_LIMIT]],
    )  # fmt: skip


_NO = _Stock("NONE", False, None, None)


def _info(
    cur: Cur, ids: list[str], day: date
) -> dict[str, tuple[str, str | None, str | None, float | None]]:  # noqa: E501
    """instrument_id -> (symbol, company, sector, close on the day)."""
    if not ids:
        return {}
    names = {
        str(r[0]): (str(r[1]), None if r[2] is None else str(r[2]))
        for r in cur.execute(
            "SELECT instrument_id, arg_max(symbol, coalesce(valid_from, DATE '1900-01-01')),"
            " arg_max(company_name, coalesce(valid_from, DATE '1900-01-01'))"
            " FROM instruments WHERE list_contains(?, instrument_id) GROUP BY 1",
            [ids],
        ).fetchall()
    }
    closes = {
        str(r[0]): _f(r[1])
        for r in cur.execute(
            "SELECT instrument_id, close_adj FROM daily_prices_adjusted_current"
            " WHERE computed_from_snapshot_id = ? AND trade_date = ?"
            " AND list_contains(?, instrument_id)",
            [LIVE, day, ids],
        ).fetchall()
    }
    sectors = classification(cur, ids)
    return {
        i: (names.get(i, (i.split("|")[-1], None))[0], names.get(i, ("", None))[1],
            sectors.get(i, (None, None))[0], closes.get(i))
        for i in ids
    }  # fmt: skip


def _stages(
    today: dict[str, _Stock],
    population: list[str],
    info: dict[str, tuple[str, str | None, str | None, float | None]],
) -> list[m.TTStageRow]:  # noqa: E501
    by_sector: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for i in population:
        stage = today[i].stage or "UNKNOWN"
        by_sector["All"][stage] += 1
        by_sector[info.get(i, ("", None, None, None))[2] or "Unclassified"][stage] += 1
    rows = [
        m.TTStageRow(sector=k, counts=dict(v), total=sum(v.values())) for k, v in by_sector.items()
    ]

    def share(r: m.TTStageRow) -> float:
        return r.counts.get("STAGE_2", 0) / r.total if r.total else 0.0

    return sorted(rows, key=lambda r: (r.sector != "All", -share(r), r.sector))
