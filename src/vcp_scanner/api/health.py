"""Market health panel (FRONTEND_SPECIFICATION 67.17): the market read the way Minervini reads
it, from our own scanned universe. Display only: nothing here feeds the regime rule, a scan,
a score or a strategy. Thresholds are display conventions, listed in the constants below."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from vcp_scanner.api import models as m
from vcp_scanner.api.context import Context
from vcp_scanner.api.queries import Cur, _f
from vcp_scanner.config.models import StageConfig
from vcp_scanner.domain.enums import WeeklyStage
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE
from vcp_scanner.features.weekly_stage import classify_weekly_stage
from vcp_scanner.paper.ledger import RULE_SET

SESSIONS_SHOWN = 470  # a 200-day average of the index plus a slope, for a year of chart
CHART_DAYS = 250  # sessions drawn in the charts
DIST_WINDOW = 25  # sessions looked at for distribution days
DIST_DROP = -0.002  # an index fall of 0.2 % or more on higher volume is a distribution day
DIST_AMBER, DIST_RED = 4, 6
BREAKOUT_LOOKBACK_DAYS = 60
FAIL_WITHIN = 5  # sessions after the breakout in which a close below the pivot is a failure
MIN_JUDGED = 5  # fewer judged breakouts than this: no colour
LEADER_RS = 80
LEADER_DAYS = 20
STAGE_WEEKS = 52  # weeks drawn in the stage chart
STAGE_FETCH_WEEKS = 124  # the 30-week average and its own 30-week look-back, on top of the chart
TRADES_JUDGED = 10  # last closed paper trades looked at; fewer: no colour

_DAILY_SQL = """
WITH runs AS (
    SELECT as_of_date, universe_snapshot_id,
           row_number() OVER (PARTITION BY as_of_date ORDER BY completed_at DESC) rn
    FROM scan_runs
    WHERE scan_type = 'TREND_TEMPLATE' AND status = 'COMPLETED'
      AND scan_config_hash = ? AND data_snapshot_id = ?
), scans AS (SELECT as_of_date, universe_snapshot_id FROM runs WHERE rn = 1),
days AS (
    SELECT DISTINCT trade_date FROM technical_features_daily
    WHERE trade_date BETWEEN ? AND ? AND calculation_version = ? AND data_snapshot_id = ?
), day_scan AS (
    SELECT d.trade_date,
           coalesce(max(s.as_of_date), (SELECT min(as_of_date) FROM scans)) AS scan_date
    FROM days d LEFT JOIN scans s ON s.as_of_date <= d.trade_date
    GROUP BY d.trade_date
)
SELECT ds.trade_date,
       count(f.sma_50),
       count(*) FILTER (WHERE f.sma_50 IS NOT NULL AND p.close_adj > f.sma_50),
       count(f.sma_200),
       count(*) FILTER (WHERE f.sma_200 IS NOT NULL AND p.close_adj > f.sma_200),
       avg(f.daily_return), sum(p.volume_adj),
       count(*) FILTER (WHERE f.daily_return > 0),
       count(*) FILTER (WHERE f.daily_return < 0),
       count(*) FILTER (WHERE f.high_252 IS NOT NULL AND p.high_adj >= f.high_252 * 0.99999),
       count(*) FILTER (WHERE f.low_252 IS NOT NULL AND p.low_adj <= f.low_252 * 1.00001)
FROM day_scan ds
JOIN scans s ON s.as_of_date = ds.scan_date
JOIN universe_memberships mem
  ON mem.universe_snapshot_id = s.universe_snapshot_id AND mem.eligible
JOIN technical_features_daily f
  ON f.instrument_id = mem.instrument_id AND f.trade_date = ds.trade_date
 AND f.calculation_version = ? AND f.data_snapshot_id = ?
JOIN daily_prices_adjusted_current p
  ON p.instrument_id = f.instrument_id AND p.trade_date = f.trade_date
 AND p.computed_from_snapshot_id = ?
GROUP BY ds.trade_date ORDER BY ds.trade_date
"""


def _item(
    id_: str, label: str, status: str, text: str, value: float | None = None,
    score: float | None = None,
) -> m.HealthItem:  # fmt: skip
    return m.HealthItem(
        id=id_, label=label, status=status, text=text, value=value,
        score=None if score is None else round(score, 1), short="",
    )  # fmt: skip


def _ramp(x: float, zero: float, hundred: float) -> float:
    """A straight line from 0 (at `zero`) to 100 (at `hundred`), held flat outside: the score of a
    reading moves a little when the reading moves a little, instead of jumping at a colour edge."""
    return max(0.0, min(100.0, (x - zero) / (hundred - zero) * 100.0))


def _sma(values: list[float], n: int, at: int) -> float | None:
    return sum(values[at - n + 1 : at + 1]) / n if at >= n - 1 else None


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0


def _index_group(
    index: list[float], mean_ret: list[float | None], volume: list[float]
) -> m.HealthGroup:
    last = len(index) - 1
    items: list[m.HealthItem] = []
    ma = {n: _sma(index, n, last) for n in (50, 150, 200)}
    if any(v is None for v in ma.values()) or last < 0:
        items.append(
            _item(
                "index_ma",
                "Index vs its averages",
                "grey",
                "Not enough history for the 50-, 150- and 200-day averages.",
            )
        )
    else:
        above = [n for n in (50, 150, 200) if index[last] > (ma[n] or 0.0)]
        prior = _sma(index, 200, last - 20)
        rising = prior is not None and (ma[200] or 0.0) > prior
        if 200 not in above:
            status = "red"
        elif len(above) == 3 and rising:
            status = "green"
        else:
            status = "amber"
        score = (
            (35 if 200 in above else 0)
            + (15 if rising else 0)
            + (20 if 150 in above else 0)
            + (20 if 50 in above else 0)
            + (10 if (ma[50] or 0.0) > (ma[150] or 0.0) else 0)
        )
        names = " and ".join(f"{n}-day" for n in above) or "none"
        items.append(_item(
            "index_ma", "Index vs its averages", status,
            f"VQI is above its: {names} average{'s' if len(above) > 1 else ''}. "
            f"The 200-day line is {'rising' if rising else 'not rising'}; the index is "
            f"{_pct(index[last], ma[200] or 1.0):+.1f}% from it.",
            _pct(index[last], ma[200] or 1.0), float(score),
        ))  # fmt: skip
    start = max(1, len(index) - DIST_WINDOW)
    dist = sum(
        1 for i in range(start, len(index))
        if (mean_ret[i] or 0.0) <= DIST_DROP and volume[i] > volume[i - 1]
    )  # fmt: skip
    status = "red" if dist >= DIST_RED else "amber" if dist >= DIST_AMBER else "green"
    items.append(_item(
        "distribution", "Distribution days", status,
        f"{dist} distribution day{'s' if dist != 1 else ''} in the last {DIST_WINDOW} sessions "
        f"(index down {-DIST_DROP * 100:.1f}% or more on higher volume; volume of our stocks, "
        "not NIFTY).", float(dist), _ramp(dist, 8, 3),
    ))  # fmt: skip
    return m.HealthGroup(id="index", title="Index price action", items=items)


def _breadth_group(rows: list[tuple[Any, ...]]) -> m.HealthGroup:
    last = rows[-1]
    items: list[m.HealthItem] = []
    n50, a50, n200, a200 = int(last[1]), int(last[2]), int(last[3]), int(last[4])
    if n50:
        p = 100.0 * a50 / n50
        status = "green" if p >= 50 else "amber" if p >= 40 else "red"
        items.append(_item("above50", "Above 50-day average", status,
                           f"{p:.1f}% of the universe closes above its 50-day average "
                           "(the regime switches on at 40%).", p, _ramp(p, 20, 60)))  # fmt: skip
    else:
        items.append(_item("above50", "Above 50-day average", "grey",
                           "No stock has a 50-day average yet."))  # fmt: skip
    if n200:
        p = 100.0 * a200 / n200
        status = "green" if p >= 50 else "amber" if p >= 35 else "red"
        items.append(
            _item(
                "above200",
                "Above 200-day average",
                status,
                f"{p:.1f}% of the universe closes above its 200-day average.",
                p,
                _ramp(p, 20, 60),
            )
        )
    else:
        items.append(_item("above200", "Above 200-day average", "grey",
                           "No stock has a 200-day average yet."))  # fmt: skip
    adv, dec = int(last[7]), int(last[8])
    line: list[float] = []
    total = 0.0
    for r in rows:
        total += float(r[7]) - float(r[8])
        line.append(total)
    ma = _sma(line, 50, len(line) - 1)
    if ma is None:
        items.append(
            _item(
                "ad",
                "Advance / decline line",
                "grey",
                f"{adv} advancing, {dec} declining; not enough history for the trend.",
            )
        )
    else:
        up = line[-1] > ma
        span = max(line[-50:]) - min(line[-50:])  # the distance from the average is judged against
        # how far the line has moved in 50 sessions
        items.append(_item(
            "ad", "Advance / decline line", "green" if up else "red",
            f"{adv} advancing, {dec} declining today; the cumulative line is "
            f"{'above' if up else 'below'} its 50-day average.", float(adv - dec),
            _ramp(line[-1] - ma, -0.5 * span, 0.5 * span) if span > 0 else 50.0,
        ))  # fmt: skip
    return m.HealthGroup(id="breadth", title="Breadth", items=items)


def _failed_breakouts(cur: Cur, ctx: Context, sessions: list[date], end: date) -> m.HealthItem:
    since = end - timedelta(days=BREAKOUT_LOOKBACK_DAYS)
    events: list[tuple[str, str, date, float]] = []
    for spec in ctx.strategies:
        for eid, iid, d, pivot in cur.execute(
            "SELECT breakout_event_id, instrument_id, breakout_date, pivot_price"
            " FROM breakout_events WHERE strategy_id = ? AND config_hash = ?"
            " AND breakout_date BETWEEN ? AND ? AND pivot_price IS NOT NULL",
            [spec.strategy_id, spec.config_hash, since, end],
        ).fetchall():
            events.append((str(eid), str(iid), d, float(pivot)))
    if not events:
        return _item("failed_breakouts", "Failed breakouts", "grey",
                     f"No breakouts in the last {BREAKOUT_LOOKBACK_DAYS} days.")  # fmt: skip
    ids = sorted({e[1] for e in events})
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    for iid, d, c in cur.execute(
        "SELECT instrument_id, trade_date, close_adj FROM daily_prices_adjusted_current"
        " WHERE computed_from_snapshot_id = ? AND trade_date >= ? AND trade_date <= ?"
        " AND instrument_id IN (SELECT unnest(?))",
        [LIVE, since, end, ids],
    ).fetchall():
        if c is not None:
            closes[str(iid)][d] = float(c)
    pos = {d: i for i, d in enumerate(sessions)}
    failed = held = pending = 0
    for _eid, iid, d, pivot in events:
        i = pos.get(d)
        if i is None:
            continue
        window = sessions[i + 1 : i + 1 + FAIL_WITHIN]
        if any(closes[iid].get(s, pivot) < pivot for s in window):
            failed += 1
        elif len(window) >= FAIL_WITHIN:
            held += 1
        else:
            pending += 1
    judged = failed + held
    if judged < MIN_JUDGED:
        return _item(
            "failed_breakouts", "Failed breakouts", "grey",
            f"Too few judged breakouts to read ({judged}; {pending} still too recent).",
            None,
        )  # fmt: skip
    rate = 100.0 * failed / judged
    status = "green" if rate <= 25 else "amber" if rate <= 50 else "red"
    return _item(
        "failed_breakouts", "Failed breakouts", status,
        f"{failed} of {judged} breakouts in the last {BREAKOUT_LOOKBACK_DAYS} days closed back "
        f"below their pivot within {FAIL_WITHIN} sessions ({rate:.0f}%); {pending} too recent "
        "to judge.", rate, _ramp(rate, 60, 10),
    )  # fmt: skip


def _leaders(cur: Cur, ctx: Context, sessions: list[date], index: list[float]) -> m.HealthItem:
    if len(sessions) <= LEADER_DAYS:
        return _item("leaders", "Leaders vs the index", "grey", "Not enough history.")
    d1, d0 = sessions[-1], sessions[-1 - LEADER_DAYS]
    ids = [
        str(r[0])
        for r in cur.execute(
            "SELECT instrument_id FROM trend_template_results WHERE config_hash = ?"
            " AND data_snapshot_id = ? AND trend_template_pass AND rs_rank >= ?"
            " AND as_of_date = (SELECT max(as_of_date) FROM trend_template_results"
            " WHERE config_hash = ? AND data_snapshot_id = ?)",
            [ctx.scan_hash, LIVE, LEADER_RS, ctx.scan_hash, LIVE],
        ).fetchall()
    ]
    if not ids:
        return _item("leaders", "Leaders vs the index", "grey", "No leaders in the latest scan.")
    px: dict[str, dict[date, float]] = defaultdict(dict)
    for iid, d, c in cur.execute(
        "SELECT instrument_id, trade_date, close_adj FROM daily_prices_adjusted_current"
        " WHERE computed_from_snapshot_id = ? AND trade_date IN (?, ?)"
        " AND instrument_id IN (SELECT unnest(?))",
        [LIVE, d0, d1, ids],
    ).fetchall():
        if c is not None:
            px[str(iid)][d] = float(c)
    rets = [_pct(v[d1], v[d0]) for v in px.values() if d0 in v and d1 in v and v[d0] > 0]
    if not rets:
        return _item("leaders", "Leaders vs the index", "grey", "No prices for the leaders.")
    lead = sum(rets) / len(rets)
    idx = _pct(index[-1], index[-1 - LEADER_DAYS])
    gap = lead - idx
    status = "green" if gap > 0 else "amber" if gap > -2 else "red"
    return _item(
        "leaders", "Leaders vs the index", status,
        f"{len(rets)} leaders (Trend Template pass, RS {LEADER_RS}+) returned {lead:+.1f}% over "
        f"{LEADER_DAYS} sessions against {idx:+.1f}% for the index "
        f"({'ahead' if gap > 0 else 'behind'} by {abs(gap):.1f} points).", gap,
        _ramp(gap, -5, 5),
    )  # fmt: skip


def _index_days(
    index: list[float], mean_ret: list[float | None], volume: list[float]
) -> m.HealthIndexDays:
    start = max(1, len(index) - DIST_WINDOW)
    days = range(start, len(index))
    ma200 = _sma(index, 200, len(index) - 1)
    ma50 = _sma(index, 50, len(index) - 1)
    return m.HealthIndexDays(
        pct_from_50=None if ma50 is None else _pct(index[-1], ma50),
        pct_from_200=None if ma200 is None else _pct(index[-1], ma200),
        accumulation=sum(
            1 for i in days if (mean_ret[i] or 0.0) >= -DIST_DROP and volume[i] > volume[i - 1]
        ),
        distribution=sum(
            1 for i in days if (mean_ret[i] or 0.0) <= DIST_DROP and volume[i] > volume[i - 1]
        ),
        window=DIST_WINDOW,
    )


def _points(rows: list[tuple[Any, ...]], index: list[float]) -> list[m.HealthPoint]:
    line: list[float] = []
    total = 0.0
    for r in rows:
        total += float(r[7]) - float(r[8])
        line.append(total)
    first = len(rows) - CHART_DAYS if len(rows) > CHART_DAYS else 0
    base = index[first] or 1.0
    out: list[m.HealthPoint] = []
    for i in range(first, len(rows)):
        r = rows[i]
        out.append(
            m.HealthPoint(
                day=r[0],
                index=index[i] / base * 100.0,
                ma50=None if (v := _sma(index, 50, i)) is None else v / base * 100.0,
                ma200=None if (v := _sma(index, 200, i)) is None else v / base * 100.0,
                highs=int(r[9]),
                lows=int(r[10]),
                above50=100.0 * int(r[2]) / int(r[1]) if r[1] else None,
                above200=100.0 * int(r[4]) / int(r[3]) if r[3] else None,
                ad_line=line[i],
                ad_ma50=_sma(line, 50, i),
            )
        )
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


def _shorts(groups: list[m.HealthGroup], last: tuple[Any, ...]) -> None:
    """The one-line form of each reading, shown on the card (the sentence is its tooltip)."""
    adv, dec, hi, lo = int(last[7]), int(last[8]), int(last[9]), int(last[10])
    for g in groups:
        for i in g.items:
            v = i.value
            if v is None and i.id != "highs_lows" and i.id != "ad":
                i.short = "not enough data yet"
            elif i.id == "index_ma":
                i.short = f"{v:+.1f}% from its 200-day"
            elif i.id == "distribution":
                i.short = f"{int(v or 0)} in the last {DIST_WINDOW} sessions"
            elif i.id == "highs_lows":
                i.short = f"{hi} highs, {lo} lows"
            elif i.id == "failed_breakouts":
                i.short = f"{v:.0f}% failed"
            elif i.id == "leaders":
                i.short = f"{v:+.1f} points vs the index"
            elif i.id in ("above50", "above200"):
                i.short = f"{v:.1f}% of stocks"
            elif i.id == "ad":
                i.short = f"{adv} up, {dec} down"
            elif i.id == "paper":
                i.short = f"{int(v or 0)} of the last {TRADES_JUDGED} won"


VERDICT_BANDS = ((70.0, "Confirmed uptrend", "green"), (45.0, "Uptrend under pressure", "amber"),
                 (25.0, "Correction", "orange"))  # fmt: skip


def _verdict(groups: list[m.HealthGroup]) -> m.HealthVerdict | None:
    items = [i for g in groups for i in g.items]
    scored = [i for i in items if i.score is not None]
    if not scored:
        return None
    score = round(sum(i.score or 0.0 for i in scored) / len(scored))
    label, status = "Downtrend", "red"
    for floor, name, colour in VERDICT_BANDS:
        if score >= floor:
            label, status = name, colour
            break
    below_200 = any(i.id == "index_ma" and i.status == "red" for i in items)
    if below_200:
        label, status = "Downtrend", "red"
    ranked = sorted(scored, key=lambda i: i.score or 0.0)
    return m.HealthVerdict(
        score=score, label=label, status=status, override=below_200,
        green=sum(i.status == "green" for i in items),
        amber=sum(i.status == "amber" for i in items),
        red=sum(i.status == "red" for i in items),
        counted=len(scored),
        weakest=[i.label for i in ranked[:3] if (i.score or 0.0) < 50],
        strongest=[i.label for i in ranked[::-1][:2] if (i.score or 0.0) >= 50],
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
    start = end - timedelta(days=int(SESSIONS_SHOWN * 7 / 5) + 10)
    rows = [
        r for r in cur.execute(
            _DAILY_SQL,
            [ctx.scan_hash, LIVE, start - timedelta(days=120), end, FEATURES_CALCULATION_VERSION,
             LIVE, FEATURES_CALCULATION_VERSION, LIVE, LIVE],
        ).fetchall()
        if r[0] <= end
    ]  # fmt: skip
    rows = rows[-SESSIONS_SHOWN:]
    if len(rows) < 2:
        return m.MarketHealthResponse(
            as_of=None,
            data_time=data_time,
            groups=[],
            points=[],
            trades=[],
            verdict=None,
            index_days=None,
        )
    level, index = 1.0, []
    for r in rows:
        if r[5] is not None:
            level *= 1.0 + float(r[5])
        index.append(level)
    sessions = [r[0] for r in rows]
    mean_ret = [_f(r[5]) for r in rows]
    volume = [float(r[6] or 0.0) for r in rows]
    index_group = _index_group(index, mean_ret, volume)
    last = rows[-1]
    hi, lo = int(last[9]), int(last[10])
    status = "green" if hi >= 2 * lo and hi > 0 else "red" if lo > hi else "amber"
    leaders_items = [
        _item("highs_lows", "New 52-week highs vs lows", status,
              f"{hi} new 52-week highs against {lo} new lows today.", float(hi - lo),
              _ramp(hi / (hi + lo), 0.3, 0.7) if hi + lo else None),
        _failed_breakouts(cur, ctx, sessions, end),
        _leaders(cur, ctx, sessions, index),
    ]  # fmt: skip
    groups = [
        index_group,
        m.HealthGroup(id="leadership", title="Leadership", items=leaders_items),
        _breadth_group(rows),
        _paper(cur, ctx),
    ]
    _shorts(groups, last)
    return m.MarketHealthResponse(
        as_of=sessions[-1], data_time=data_time, groups=groups,
        points=_points(rows, index), trades=_trades(cur, ctx), verdict=_verdict(groups),
        index_days=_index_days(index, mean_ret, volume), stages=_stage_history(cur, ctx, end),
    )  # fmt: skip
