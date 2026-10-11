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
from vcp_scanner.api.queries import Cur, _f, classification
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
FAIL_WITHIN = 5  # sessions after the breakout in which a close below the pivot is a failure
MIN_JUDGED = 5  # fewer judged breakouts than this: no colour
LEADER_RS = 80
LEADER_DAYS = 20
STAGE_WEEKS = 52  # weeks drawn in the stage chart
STAGE_FETCH_WEEKS = 124  # the 30-week average and its own 30-week look-back, on top of the chart
TRADES_JUDGED = 10  # last closed paper trades looked at; fewer: no colour
FLOW_WINDOW = 20  # sessions of judged breakouts behind each point of the failure-rate line
NET_HIGHS_AVG = 10  # sessions in the average of net new highs (the reading scores this)
YEAR = 252  # sessions in 52 weeks
NEAR_HIGH = 0.03  # the index within 3 % of its 52-week high
DIVERGENCE_GAP = 0.25  # a line "clearly below" its 52-week high: by a quarter of its range
NSE_LINES = {"nifty50": "Nifty 50", "nifty500": "Nifty 500"}  # index_daily names
SECTOR_MONTH = 21  # sessions in "a month" for the sector table
THRUST_DAYS = 10  # breadth thrust: the 10-day average of advancers / (advancers + decliners)
THRUST_LOW, THRUST_HIGH = 40.0, 61.5  # rising from below 40 % to above 61.5 % within 10 sessions
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


def _day_kinds(mean_ret: list[float | None], volume: list[float]) -> list[str | None]:
    """'distribution' when VQI fell DIST_DROP or more on higher volume than the day before,
    'accumulation' when it rose as much on higher volume, else None. The one rule behind the
    reading, the figures beside the chart and the chart's markers."""
    out: list[str | None] = [None]
    for i in range(1, len(mean_ret)):
        r = mean_ret[i] or 0.0
        heavier = volume[i] > volume[i - 1]
        out.append(
            "distribution" if heavier and r <= DIST_DROP
            else "accumulation" if heavier and r >= -DIST_DROP else None
        )  # fmt: skip
    return out[: len(mean_ret)]


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
    kinds = _day_kinds(mean_ret, volume)
    dist = sum(k == "distribution" for k in kinds[max(1, len(index) - DIST_WINDOW) :])
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


def _failure_item(flow: list[m.HealthFlowPoint]) -> m.HealthItem:
    """The failed-breakout reading of the score: the latest point of the setup-flow line, so the
    score and the Setups and breakouts card show one number."""
    last = flow[-1] if flow else None
    if last is None or last.failed_pct is None:
        return _item(
            "failed_breakouts", "Failed breakouts", "grey",
            f"Too few breakouts judged in the last {FLOW_WINDOW} sessions to read "
            f"({last.judged if last else 0}).",
        )  # fmt: skip
    rate = last.failed_pct
    status = "green" if rate <= 25 else "amber" if rate <= 50 else "red"
    return _item(
        "failed_breakouts", "Failed breakouts", status,
        f"{last.failed} of {last.judged} breakouts judged in the last {FLOW_WINDOW} sessions "
        f"closed back below their pivot within {FAIL_WITHIN} sessions ({rate:.0f}%); a stock "
        "breaking out in several strategies on one day counts once.", rate, _ramp(rate, 60, 10),
    )  # fmt: skip


def _leader_counts(cur: Cur, ctx: Context, sessions: list[date]) -> tuple[int | None, int | None]:
    """Stocks passing the Trend Template with RS LEADER_RS+ in the latest scan, and the change
    against the scan LEADER_DAYS sessions earlier (None until that scan exists)."""
    counts = dict(cur.execute(
        "SELECT as_of_date, count(*) FILTER (WHERE trend_template_pass AND rs_rank >= ?)"
        " FROM trend_template_results WHERE config_hash = ? AND data_snapshot_id = ?"
        " AND as_of_date <= ? GROUP BY 1",
        [LEADER_RS, ctx.scan_hash, LIVE, sessions[-1]],
    ).fetchall())  # fmt: skip
    if not counts:
        return None, None
    latest = max(counts)
    i = sessions.index(latest) if latest in sessions else None
    then = sessions[i - LEADER_DAYS] if i is not None and i >= LEADER_DAYS else None
    now = int(counts[latest])
    return now, (now - int(counts[then]) if then in counts else None)


def _highs_lows(rows: list[tuple[Any, ...]]) -> m.HealthItem:
    """New 52-week highs against lows, read on their 10-session averages (one day is noisy)."""
    recent = rows[-NET_HIGHS_AVG:]
    hi_today, lo_today = int(rows[-1][9]), int(rows[-1][10])
    hi = sum(int(r[9]) for r in recent) / len(recent)
    lo = sum(int(r[10]) for r in recent) / len(recent)
    status = "green" if hi >= 2 * lo and hi > 0 else "red" if lo > hi else "amber"
    return _item(
        "highs_lows", "New 52-week highs vs lows", status,
        f"{hi_today} new 52-week highs against {lo_today} new lows today; over the last "
        f"{len(recent)} sessions {hi:.1f} highs against {lo:.1f} lows a day "
        f"(net {hi - lo:+.1f}).", hi - lo,
        _ramp(hi / (hi + lo), 0.3, 0.7) if hi + lo else None,
    )  # fmt: skip


def _below_own_high(series: list[float | None]) -> tuple[bool, float] | None:
    """(clearly below its 52-week high, how far as a share of the range) of a line, or None
    when it has no range to judge."""
    year = [v for v in series[-YEAR:] if v is not None]
    last = series[-1] if series else None
    if len(year) < 2 or last is None:
        return None
    top, bottom = max(year), min(year)
    if top <= bottom:
        return None
    gap = (top - last) / (top - bottom)
    return gap > DIVERGENCE_GAP, gap


def _warnings(rows: list[tuple[Any, ...]], index: list[float]) -> list[m.HealthWarning]:
    """Divergences: VQI within NEAR_HIGH of its 52-week high while the A/D line, or the 10-day
    average of net new highs, is clearly below its own 52-week high (fewer stocks carry the
    index up). Shown as warnings; not part of the score."""
    if len(index) < 2 or index[-1] < (1 - NEAR_HIGH) * max(index[-YEAR:]):
        return []
    out: list[m.HealthWarning] = []
    off = _pct(index[-1], max(index[-YEAR:]))
    ad = _below_own_high(list(_ad_line(rows)))
    if ad and ad[0]:
        out.append(m.HealthWarning(id="ad_divergence", text=(
            f"VQI is {abs(off):.1f}% from its 52-week high, but the advance/decline line is "
            f"{ad[1] * 100:.0f}% of its 52-week range below its own high: fewer stocks are "
            "taking part in the advance.")))  # fmt: skip
    net = _net_highs(rows)
    avg = [_sma(net, NET_HIGHS_AVG, i) for i in range(len(net))]
    nh = _below_own_high(avg)
    if nh and nh[0]:
        out.append(m.HealthWarning(id="highs_divergence", text=(
            f"VQI is {abs(off):.1f}% from its 52-week high, but the 10-day average of net new "
            f"highs is {nh[1] * 100:.0f}% of its 52-week range below its own high: fewer "
            "stocks are making new highs.")))  # fmt: skip
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
    exists = cur.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'index_daily'"
    ).fetchone()
    if exists and exists[0]:
        closes: dict[str, dict[date, float]] = defaultdict(dict)
        for name, d, c in cur.execute(
            "SELECT index_name, trade_date, close FROM index_daily WHERE trade_date IN (?, ?)"
            " AND index_name IN (SELECT unnest(?))",
            [end, then, list(SECTOR_INDEX.values())],
        ).fetchall():
            closes[str(name)][d] = float(c)
        index_change = {n: _pct(v[end], v[then]) for n, v in closes.items()
                        if end in v and then in v and v[then]}  # fmt: skip
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
        ))  # fmt: skip
    out.sort(key=lambda x: (x.sector is None, -(x.above50_pct or -1.0), x.sector or ""))
    return out


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
    recent = _day_kinds(mean_ret, volume)[max(1, len(index) - DIST_WINDOW) :]
    ma200 = _sma(index, 200, len(index) - 1)
    ma50 = _sma(index, 50, len(index) - 1)
    return m.HealthIndexDays(
        pct_from_50=None if ma50 is None else _pct(index[-1], ma50),
        pct_from_200=None if ma200 is None else _pct(index[-1], ma200),
        accumulation=sum(k == "accumulation" for k in recent),
        distribution=sum(k == "distribution" for k in recent),
        window=DIST_WINDOW,
    )


def _ad_line(rows: list[tuple[Any, ...]]) -> list[float]:
    line: list[float] = []
    total = 0.0
    for r in rows:
        total += float(r[7]) - float(r[8])
        line.append(total)
    return line


def _net_highs(rows: list[tuple[Any, ...]]) -> list[float]:
    return [float(int(r[9]) - int(r[10])) for r in rows]


def _nse_closes(cur: Cur, start: date, end: date) -> dict[str, dict[date, float]]:
    """NIFTY 50 / 500 closes by day (empty before ``vcp indices update`` has run)."""
    exists = cur.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'index_daily'"
    ).fetchone()
    out: dict[str, dict[date, float]] = {k: {} for k in NSE_LINES}
    if not exists or not exists[0]:
        return out
    names = {v.lower(): k for k, v in NSE_LINES.items()}
    for name, d, close in cur.execute(
        "SELECT index_name, trade_date, close FROM index_daily WHERE trade_date BETWEEN ? AND ?"
        " AND lower(index_name) IN (SELECT unnest(?))",
        [start, end, list(names)],
    ).fetchall():
        out[names[str(name).lower()]][d] = float(close)
    return out


def _rebased(closes: dict[date, float], days: list[date]) -> list[float | None]:
    """The closes of ``days``, 100 at the first day that has one; None where a day has none."""
    base = next((closes[d] for d in days if d in closes), None)
    return [None if base is None or d not in closes else closes[d] / base * 100.0 for d in days]


def _thrust(rows: list[tuple[Any, ...]]) -> tuple[list[float | None], list[bool]]:
    """The breadth-thrust ratio per session and the sessions a thrust is signalled: the day the
    ratio first closes above THRUST_HIGH with a reading below THRUST_LOW in the THRUST_DAYS
    sessions before it (Zweig's rule, on our universe). Display only."""
    share = [
        100.0 * float(r[7]) / (float(r[7]) + float(r[8])) if float(r[7]) + float(r[8]) else None
        for r in rows
    ]
    ratio: list[float | None] = []
    for i in range(len(share)):
        window = share[max(0, i - THRUST_DAYS + 1) : i + 1]
        full = i >= THRUST_DAYS - 1 and all(v is not None for v in window)
        ratio.append(sum(v for v in window if v is not None) / THRUST_DAYS if full else None)
    signal = [False] * len(ratio)
    for i in range(1, len(ratio)):
        now, before = ratio[i], ratio[i - 1]
        if now is None or now <= THRUST_HIGH or (before is not None and before > THRUST_HIGH):
            continue
        recent = [v for v in ratio[max(0, i - THRUST_DAYS) : i] if v is not None]
        signal[i] = any(v < THRUST_LOW for v in recent)
    return ratio, signal


def _points(
    rows: list[tuple[Any, ...]], index: list[float],
    nse: dict[str, dict[date, float]] | None = None,
    kinds: list[str | None] | None = None,
) -> list[m.HealthPoint]:  # fmt: skip
    line = _ad_line(rows)
    net = _net_highs(rows)
    ratio, signal = _thrust(rows)
    first = len(rows) - CHART_DAYS if len(rows) > CHART_DAYS else 0
    base = index[first] or 1.0
    days = [r[0] for r in rows[first:]]
    lines = {k: _rebased((nse or {}).get(k, {}), days) for k in NSE_LINES}
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
                net_highs=int(r[9]) - int(r[10]),
                net_highs_avg10=_sma(net, NET_HIGHS_AVG, i),
                nifty50=lines["nifty50"][i - first],
                nifty500=lines["nifty500"][i - first],
                distribution=kinds is not None and kinds[i] == "distribution",
                thrust_ratio=ratio[i],
                thrust=signal[i],
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
    adv, dec = int(last[7]), int(last[8])
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
                i.short = f"net {v:+.1f} a day (10 sessions)"
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
    flow = _flow(cur, ctx, sessions)
    leader_count, leader_change = _leader_counts(cur, ctx, sessions)
    leaders_items = [
        _highs_lows(rows),
        _failure_item(flow),
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
        points=_points(rows, index, _nse_closes(cur, sessions[0], end),
                       _day_kinds(mean_ret, volume)),
        trades=_trades(cur, ctx), verdict=_verdict(groups),
        index_days=_index_days(index, mean_ret, volume), stages=_stage_history(cur, ctx, end),
        flow=flow, warnings=_warnings(rows, index), leader_count=leader_count,
        leader_change=leader_change,
        new_highs=_new_highs_today(cur, ctx, end), sectors=_sectors(cur, ctx, sessions),
    )  # fmt: skip
