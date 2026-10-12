"""Market health panel (FRONTEND_SPECIFICATION 67.17): the market read the way Minervini reads
it, from our own scanned universe. Display only: nothing here feeds the regime rule, a scan,
a score or a strategy. Thresholds are display conventions, listed in the constants below."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from vcp_scanner.api import models as m
from vcp_scanner.api.context import Context
from vcp_scanner.api.health_score import health_score, score_points, score_warnings
from vcp_scanner.api.queries import Cur, classification
from vcp_scanner.api.rotation import sector_rotation
from vcp_scanner.config.models import StageConfig
from vcp_scanner.domain.enums import WeeklyStage
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE
from vcp_scanner.features.weekly_stage import classify_weekly_stage
from vcp_scanner.market_health.features import Series
from vcp_scanner.paper.ledger import RULE_SET

SESSIONS_SHOWN = 470  # a 200-day average of the index plus a slope, for a year of chart
CHART_DAYS = 250  # sessions drawn in the charts
FAIL_WITHIN = 5  # sessions after the breakout in which a close below the pivot is a failure
MIN_JUDGED = 5  # fewer judged breakouts than this: no colour
STAGE_WEEKS = 52  # weeks drawn in the stage chart
STAGE_FETCH_WEEKS = 124  # the 30-week average and its own 30-week look-back, on top of the chart
TRADES_JUDGED = 10  # last closed paper trades looked at; fewer: no colour
FLOW_WINDOW = 20  # sessions of judged breakouts behind each point of the failure-rate line
SECTOR_MONTH = 21  # sessions in "a month" for the sector table
#: the score's sector horizons (config/market_health.yaml leadership.sector_horizons; a test
#: keeps them equal) and enough calendar days for a 50-day average 126 sessions back
SECTOR_HORIZONS = (21, 63, 126)
SECTOR_FETCH_DAYS = 280
#: NSE's sector (the Industry column of its index lists) -> its sector index in the daily file.
#: Diversified, Forest Materials, Textiles and Utilities have none.
SECTOR_INDEX = {
    "Automobile and Auto Components": "Nifty Auto", "Capital Goods": "Nifty Capital Goods",
    "Chemicals": "Nifty Chemicals", "Construction": "Nifty Construction",
    "Construction Materials": "Nifty Cement", "Consumer Durables": "Nifty Consumer Durables",
    "Consumer Services": "Nifty Consumer Services", "Fast Moving Consumer Goods": "Nifty FMCG",
    "Financial Services": "Nifty Financial Services", "Healthcare": "Nifty Healthcare Index",
    "Information Technology": "Nifty IT", "Media Entertainment & Publication": "Nifty Media",
    "Metals & Mining": "Nifty Metal", "Oil Gas & Consumable Fuels": "Nifty Oil & Gas",
    "Power": "Nifty Power", "Realty": "Nifty Realty", "Services": "Nifty Services Sector",
    "Telecommunication": "Nifty Telecommunications",
}  # fmt: skip


def _item(
    id_: str, label: str, status: str, text: str, value: float | None = None,
    score: float | None = None,
) -> m.HealthItem:  # fmt: skip
    return m.HealthItem(
        id=id_, label=label, status=status, text=text, value=value,
        score=None if score is None else round(score, 1), short="",
    )  # fmt: skip


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0


def _judge(
    closes: dict[date, float], sessions: list[date], i: int, pivot: float
) -> tuple[bool, int] | None:
    """(failed, index of the session the result is known) of a breakout at ``sessions[i]``, or
    None while it is too recent: failed on the first close below the pivot within FAIL_WITHIN
    sessions, held once FAIL_WITHIN sessions have closed above it. Known only from that day on,
    so a chart point never uses a later close."""
    for k in range(i + 1, min(i + 1 + FAIL_WITHIN, len(sessions))):
        if closes.get(sessions[k], pivot) < pivot:
            return True, k
    return (False, i + FAIL_WITHIN) if i + FAIL_WITHIN < len(sessions) else None


def _flow(cur: Cur, ctx: Context, sessions: list[date]) -> list[m.HealthFlowPoint]:
    """New setups and breakouts per session and the failure rate of recent breakouts, from the
    first stored scan on (earlier days hold only the breakouts the first scans found looking
    back, so they would undercount)."""
    scans: dict[str, list[date]] = {}
    eligible: dict[tuple[str, date], set[str]] = defaultdict(set)
    for spec in ctx.strategies:
        rows = cur.execute(
            "SELECT as_of_date, instrument_id, eligible FROM setup_scores WHERE strategy_id = ?"
            " AND config_hash = ? AND data_snapshot_id = ?",
            [spec.strategy_id, spec.config_hash, LIVE],
        ).fetchall()
        scans[spec.strategy_id] = sorted({d for d, _i, _e in rows})
        for d, iid, ok in rows:
            if ok:
                eligible[(spec.strategy_id, d)].add(str(iid))
    first = min((d[0] for d in scans.values() if d), default=None)
    if first is None:
        return []
    days = [d for d in sessions[-CHART_DAYS:] if d >= first]
    new: dict[date, int] = defaultdict(int)
    scanned: set[date] = set()
    for sid, dates in scans.items():
        scanned.update(dates)
        for prev, d in zip(dates, dates[1:], strict=False):
            new[d] += len(eligible[(sid, d)] - eligible[(sid, prev)])
    events: list[tuple[str, date, float]] = []
    for spec in ctx.strategies:
        events += [
            (str(iid), d, float(pivot))
            for iid, d, pivot in cur.execute(
                "SELECT instrument_id, breakout_date, pivot_price FROM breakout_events"
                " WHERE strategy_id = ? AND config_hash = ? AND breakout_date >= ?"
                " AND pivot_price IS NOT NULL",
                [spec.strategy_id, spec.config_hash, first],
            ).fetchall()
        ]  # fmt: skip
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    ids = sorted({e[0] for e in events})
    if ids:
        for iid, d, c in cur.execute(
            "SELECT instrument_id, trade_date, close_adj FROM daily_prices_adjusted_current"
            " WHERE computed_from_snapshot_id = ? AND trade_date >= ?"
            " AND instrument_id IN (SELECT unnest(?))",
            [LIVE, first, ids],
        ).fetchall():
            if c is not None:
                closes[str(iid)][d] = float(c)
    # A stock that breaks out in several strategies on one day counts once, judged against the
    # lowest of its pivots: it fails when it closes back below every strategy's pivot.
    pivots: dict[tuple[str, date], float] = {}
    for iid, d, pivot in events:
        pivots[(iid, d)] = min(pivot, pivots.get((iid, d), pivot))
    pos = {d: i for i, d in enumerate(sessions)}
    count: dict[date, int] = defaultdict(int)
    known: list[tuple[int, bool]] = []  # (session index the result is known, failed)
    for (iid, d), pivot in pivots.items():
        count[d] += 1
        i = pos.get(d)
        if i is not None and (result := _judge(closes[iid], sessions, i, pivot)) is not None:
            known.append((result[1], result[0]))
    out = []
    for d in days:
        i = pos[d]
        recent = [failed for k, failed in known if i - FLOW_WINDOW < k <= i]
        out.append(m.HealthFlowPoint(
            day=d, new_setups=None if d == first or d not in scanned else new[d],
            breakouts=count[d], judged=len(recent), failed=sum(recent),
            failed_pct=100.0 * sum(recent) / len(recent) if len(recent) >= MIN_JUDGED else None,
        ))  # fmt: skip
    return out


_NEW_HIGHS_SQL = """
WITH snap AS (
    SELECT universe_snapshot_id FROM scan_runs
    WHERE scan_type = 'TREND_TEMPLATE' AND status = 'COMPLETED'
      AND scan_config_hash = ? AND data_snapshot_id = ? AND as_of_date <= ?
    ORDER BY as_of_date DESC, completed_at DESC LIMIT 1
), rs AS (
    SELECT instrument_id, rs_rank FROM trend_template_results
    WHERE config_hash = ? AND data_snapshot_id = ? AND as_of_date = (
        SELECT max(as_of_date) FROM trend_template_results
        WHERE config_hash = ? AND data_snapshot_id = ? AND as_of_date <= ?)
)
SELECT f.instrument_id, coalesce(i.symbol, f.instrument_id), rs.rs_rank
FROM technical_features_daily f
JOIN universe_memberships mem ON mem.instrument_id = f.instrument_id AND mem.eligible
 AND mem.universe_snapshot_id = (SELECT universe_snapshot_id FROM snap)
JOIN daily_prices_adjusted_current p ON p.instrument_id = f.instrument_id
 AND p.trade_date = f.trade_date AND p.computed_from_snapshot_id = ?
LEFT JOIN instruments i ON i.instrument_id = f.instrument_id
LEFT JOIN rs ON rs.instrument_id = f.instrument_id
WHERE f.trade_date = ? AND f.calculation_version = ? AND f.data_snapshot_id = ?
  AND f.high_252 IS NOT NULL AND p.high_adj >= f.high_252 * 0.99999
"""


def _new_highs_today(cur: Cur, ctx: Context, end: date) -> list[m.HealthNewHigh]:
    """Universe stocks whose high on ``end`` is a new 52-week high (the same test as the daily
    count), best RS first."""
    rows = cur.execute(
        _NEW_HIGHS_SQL,
        [ctx.scan_hash, LIVE, end, ctx.scan_hash, LIVE, ctx.scan_hash, LIVE, end, LIVE, end,
         FEATURES_CALCULATION_VERSION, LIVE],
    ).fetchall()  # fmt: skip
    sectors = classification(cur, [str(r[0]) for r in rows])
    out = [
        m.HealthNewHigh(symbol=str(r[1]), rs_rank=None if r[2] is None else int(r[2]),
                        sector=sectors.get(str(r[0]), (None, None))[0])
        for r in rows
    ]  # fmt: skip
    out.sort(key=lambda x: (-(x.rs_rank if x.rs_rank is not None else -1), x.symbol))
    return out


_SECTOR_SQL = """
WITH snap AS (
    SELECT universe_snapshot_id FROM scan_runs
    WHERE scan_type = 'TREND_TEMPLATE' AND status = 'COMPLETED'
      AND scan_config_hash = ? AND data_snapshot_id = ? AND as_of_date <= ?
    ORDER BY as_of_date DESC, completed_at DESC LIMIT 1
)
SELECT f.instrument_id, f.trade_date,
       f.sma_50 IS NOT NULL AND p.close_adj > f.sma_50,
       f.sma_50 IS NOT NULL,
       f.high_252 IS NOT NULL AND p.high_adj >= f.high_252 * 0.99999
FROM technical_features_daily f
JOIN universe_memberships mem ON mem.instrument_id = f.instrument_id AND mem.eligible
 AND mem.universe_snapshot_id = (SELECT universe_snapshot_id FROM snap)
JOIN daily_prices_adjusted_current p ON p.instrument_id = f.instrument_id
 AND p.trade_date = f.trade_date AND p.computed_from_snapshot_id = ?
WHERE f.trade_date IN (?, ?) AND f.calculation_version = ? AND f.data_snapshot_id = ?
"""


def _index_uptrend(closes: dict[date, float], end: date) -> list[bool | None]:
    """The Market Health Score's sector test on one index, per SECTOR_HORIZONS: above its 50-day
    average with a positive return over the horizon; None without the history."""
    sr = Series([(d, c, None) for d, c in sorted(closes.items())])
    j = sr.pos.get(end)
    if j is None:
        return [None] * len(SECTOR_HORIZONS)
    ma = sr.sma(j, 50)
    out: list[bool | None] = []
    for h in SECTOR_HORIZONS:
        r = sr.ret(j, h)
        out.append(None if ma is None or r is None else sr.close[j] > ma and r > 0)
    return out


def _sectors(cur: Cur, ctx: Context, sessions: list[date]) -> list[m.HealthSector]:
    """Today's universe by NSE sector: share above the 50-day average now and a month ago (the
    same stocks both days), median RS, stocks with a breakout in the month, new highs today, and
    the sector index's change. Unclassified stocks form one row of their own."""
    end = sessions[-1]
    then = sessions[-1 - SECTOR_MONTH] if len(sessions) > SECTOR_MONTH else sessions[0]
    rows = cur.execute(
        _SECTOR_SQL,
        [ctx.scan_hash, LIVE, end, LIVE, end, then, FEATURES_CALCULATION_VERSION, LIVE],
    ).fetchall()
    if not rows:
        return []
    now: dict[str, tuple[bool, bool, bool]] = {}
    past: dict[str, tuple[bool, bool]] = {}
    for iid, d, above, has_ma, high in rows:
        if d == end:
            now[str(iid)] = (bool(above), bool(has_ma), bool(high))
        else:
            past[str(iid)] = (bool(above), bool(has_ma))
    ids = sorted(now)
    sector_of = {i: s for i, (s, _ind) in classification(cur, ids).items()}
    rs = {
        str(r[0]): int(r[1])
        for r in cur.execute(
            "SELECT instrument_id, rs_rank FROM trend_template_results WHERE config_hash = ?"
            " AND data_snapshot_id = ? AND rs_rank IS NOT NULL AND as_of_date = ("
            "SELECT max(as_of_date) FROM trend_template_results WHERE config_hash = ?"
            " AND data_snapshot_id = ? AND as_of_date <= ?)",
            [ctx.scan_hash, LIVE, ctx.scan_hash, LIVE, end],
        ).fetchall()
    }
    broke: set[str] = set()
    since = sessions[-SECTOR_MONTH] if len(sessions) >= SECTOR_MONTH else sessions[0]
    for spec in ctx.strategies:
        broke |= {
            str(r[0])
            for r in cur.execute(
                "SELECT DISTINCT instrument_id FROM breakout_events WHERE strategy_id = ?"
                " AND config_hash = ? AND breakout_date BETWEEN ? AND ?",
                [spec.strategy_id, spec.config_hash, since, end],
            ).fetchall()
        }
    index_change: dict[str, float] = {}
    uptrend: dict[str, list[bool | None]] = {}
    exists = cur.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'index_daily'"
    ).fetchone()
    if exists and exists[0]:
        closes: dict[str, dict[date, float]] = defaultdict(dict)
        for name, d, c in cur.execute(
            "SELECT index_name, trade_date, close FROM index_daily WHERE trade_date BETWEEN ? AND ?"
            " AND close > 0 AND index_name IN (SELECT unnest(?))",
            [end - timedelta(days=SECTOR_FETCH_DAYS), end, list(SECTOR_INDEX.values())],
        ).fetchall():
            closes[str(name)][d] = float(c)
        index_change = {n: _pct(v[end], v[then]) for n, v in closes.items()
                        if end in v and then in v and v[then]}  # fmt: skip
        uptrend = {n: _index_uptrend(v, end) for n, v in closes.items()}
    groups: dict[str | None, list[str]] = defaultdict(list)
    for iid in ids:
        groups[sector_of.get(iid)].append(iid)
    out: list[m.HealthSector] = []
    for sector, members in groups.items():
        with_ma = [i for i in members if now[i][1]]
        above = sum(now[i][0] for i in with_ma)
        pct = 100.0 * above / len(with_ma) if with_ma else None
        before = [i for i in with_ma if i in past and past[i][1]]
        pct_then = 100.0 * sum(past[i][0] for i in before) / len(before) if before else None
        ranks = sorted(rs[i] for i in members if i in rs)
        median = None if not ranks else (ranks[(len(ranks) - 1) // 2] + ranks[len(ranks) // 2]) / 2
        name = SECTOR_INDEX.get(sector or "")
        out.append(m.HealthSector(
            sector=sector, stocks=len(with_ma), above50_pct=pct,
            above50_change=None if pct is None or pct_then is None else pct - pct_then,
            median_rs=median, breakouts=sum(i in broke for i in members),
            new_highs=sum(now[i][2] for i in members), index_name=name,
            index_change_pct=index_change.get(name) if name else None,
            index_uptrend=uptrend.get(name, []) if name else [],
        ))  # fmt: skip
    out.sort(key=lambda x: (x.sector is None, -(x.above50_pct or -1.0), x.sector or ""))
    return out


def _trades(cur: Cur, ctx: Context) -> list[m.HealthTrade]:
    rows = cur.execute(
        "SELECT event_date, metadata_json FROM paper_events WHERE rule_set = ?"
        " AND event_type = 'EXIT' AND config_hash IN (SELECT unnest(?))"
        " ORDER BY event_date DESC, recorded_at DESC",
        [RULE_SET, [s.config_hash for s in ctx.strategies]],
    ).fetchall()
    out: list[m.HealthTrade] = []
    for d, meta in rows[:TRADES_JUDGED]:
        try:
            out.append(m.HealthTrade(day=d, ret_pct=float(json.loads(meta or "{}")["ret_pct"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out[::-1]


def _paper(cur: Cur, ctx: Context) -> m.HealthGroup:
    rows = cur.execute(
        "SELECT event_date, metadata_json FROM paper_events WHERE rule_set = ?"
        " AND event_type = 'EXIT' AND config_hash IN (SELECT unnest(?))"
        " ORDER BY event_date DESC, recorded_at DESC",
        [RULE_SET, [s.config_hash for s in ctx.strategies]],
    ).fetchall()
    rets: list[float] = []
    for _d, meta in rows[:TRADES_JUDGED]:
        try:
            rets.append(float(json.loads(meta or "{}")["ret_pct"]))
        except (KeyError, ValueError, TypeError):
            continue
    n = len(rets)
    wins = sum(r > 0 for r in rets)
    if n < TRADES_JUDGED:
        item = _item(
            "paper", "Our paper trades", "grey",
            f"{n} closed paper trade{'s' if n != 1 else ''} so far; the last {TRADES_JUDGED} are "
            "needed for a read.", None,
        )  # fmt: skip
    else:
        status = "green" if wins >= 6 else "amber" if wins >= 4 else "red"
        item = _item(
            "paper", "Our paper trades", status,
            f"{wins} of the last {n} closed paper trades were winners (average "
            f"{sum(rets) / n:+.1f}%), all five strategies together.", float(wins),
            wins * 10.0,
        )  # fmt: skip
    return m.HealthGroup(id="feedback", title="Feedback loop", items=[item])


def _shorts(groups: list[m.HealthGroup]) -> None:
    """The one-line form of each reading, shown on the card (the sentence is its tooltip)."""
    for g in groups:
        for i in g.items:
            if i.id == "paper":
                i.short = (
                    "not enough data yet" if i.value is None
                    else f"{int(i.value)} of the last {TRADES_JUDGED} won"
                )  # fmt: skip


def _stage_history(cur: Cur, ctx: Context, end: date) -> list[m.HealthStagePoint]:
    """The weekly stage of the stocks in the latest scan at the end of each of the last weeks.

    Same rule as the scan (``classify_weekly_stage``), read from stored daily prices: a week's
    close is the last close of that week, the current week uses the latest close. The list of
    stocks is today's, so the chart shows how today's scanned stocks have moved through the
    stages, not who was in the scan then."""
    ids = [
        str(r[0])
        for r in cur.execute(
            "SELECT instrument_id FROM trend_template_results WHERE config_hash = ?"
            " AND data_snapshot_id = ? AND as_of_date = (SELECT max(as_of_date) FROM"
            " trend_template_results WHERE config_hash = ? AND data_snapshot_id = ?)",
            [ctx.scan_hash, LIVE, ctx.scan_hash, LIVE],
        ).fetchall()
    ]
    if not ids:
        return []
    since = end - timedelta(weeks=STAGE_FETCH_WEEKS)
    weekly: dict[str, list[tuple[date, float]]] = defaultdict(list)
    week_end: dict[date, date] = {}
    for iid, wk, last_day, c in cur.execute(
        "SELECT instrument_id, CAST(date_trunc('week', trade_date) AS DATE), max(trade_date),"
        " arg_max(close_adj, trade_date) FROM daily_prices_adjusted_current"
        " WHERE computed_from_snapshot_id = ? AND trade_date BETWEEN ? AND ?"
        " AND close_adj IS NOT NULL AND instrument_id IN (SELECT unnest(?))"
        " GROUP BY 1, 2 ORDER BY 1, 2",
        [LIVE, since, end, ids],
    ).fetchall():
        weekly[str(iid)].append((wk, float(c)))
        week_end[wk] = max(week_end.get(wk, last_day), last_day)
    weeks = sorted({wk for rows in weekly.values() for wk, _ in rows})[-STAGE_WEEKS:]
    if not weeks:
        return []
    cfg = StageConfig()
    by_week: dict[date, dict[WeeklyStage, int]] = {w: defaultdict(int) for w in weeks}
    for rows in weekly.values():
        closes = [c for _, c in rows]
        for k, (wk, _c) in enumerate(rows):
            if wk in by_week:
                by_week[wk][classify_weekly_stage(closes[: k + 1], cfg).stage] += 1
    out: list[m.HealthStagePoint] = []
    for w in weeks:
        n = by_week[w]
        counts = [
            n[WeeklyStage.STAGE_1], n[WeeklyStage.STAGE_2], n[WeeklyStage.STAGE_3],
            n[WeeklyStage.STAGE_4], n[WeeklyStage.TRANSITION],
        ]  # fmt: skip
        out.append(m.HealthStagePoint(
            day=week_end[w], stage1=counts[0], stage2=counts[1], stage3=counts[2], stage4=counts[3],
            transition=counts[4], total=sum(counts),
        ))  # fmt: skip
    return out


def market_health(cur: Cur, ctx: Context, end: date, data_time: datetime) -> m.MarketHealthResponse:
    """The Market Overview's cards. The index, breadth, leadership and breakout readings come from
    the stored Market Health Score (one source, FRONTEND_SPECIFICATION §67.34); the setup flow,
    sector table, stages, new highs and paper trades are read here."""
    start = end - timedelta(days=int(SESSIONS_SHOWN * 7 / 5) + 10)
    sessions = [
        r[0] for r in cur.execute(
            "SELECT DISTINCT trade_date FROM technical_features_daily WHERE trade_date BETWEEN ?"
            " AND ? AND calculation_version = ? AND data_snapshot_id = ? ORDER BY 1",
            [start, end, FEATURES_CALCULATION_VERSION, LIVE],
        ).fetchall()
    ]  # fmt: skip
    if len(sessions) < 2:
        return m.MarketHealthResponse(as_of=None, data_time=data_time, groups=[], trades=[])
    groups = [_paper(cur, ctx)]
    _shorts(groups)
    score = health_score(cur, end)
    return m.MarketHealthResponse(
        as_of=sessions[-1], data_time=data_time, groups=groups, trades=_trades(cur, ctx),
        score=score, score_points=score_points(cur, end),
        stages=_stage_history(cur, ctx, end), flow=_flow(cur, ctx, sessions),
        warnings=score_warnings(score), new_highs=_new_highs_today(cur, ctx, end),
        sectors=_sectors(cur, ctx, sessions), rotation=sector_rotation(cur, end),
    )  # fmt: skip
