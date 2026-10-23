"""Superperformance study (owner, 2026-10-23): how past leaders behaved before, during and at the
end of their big runs. Research and display only; it changes no strategy, score, scan or ledger.

A *run* is a stretch where a stock closed at least ``min_gain`` above an eligible low within
``horizon`` sessions. The *launch* is that low (the lowest eligible close the run started from),
the *peak* the highest close within ``horizon`` sessions after it; the next run of the stock is
looked for after the peak. All prices are adjusted closes.

Every feature is measured with data known on the day it describes (no look-ahead): the universe,
RS rank and Trend Template are rebuilt per day (``superperformance_data``), the weekly stage uses
the completed weeks plus the day's close, fundamentals and shareholding are taken as available
that day. Winners are compared with all other eligible stocks on the same days, in two frames:

* ``launch``: stocks on their launch day against every other eligible stock that day;
* ``breakout``: every eligible breakout (first close above the highest close of the prior
  ``base_lookback`` sessions, at least ``min_base`` sessions after it) against the others the same
  day; a breakout is a winner when the stock later closes ``min_gain`` above the breakout close
  within ``horizon`` sessions.

Lift is the Mantel-Haenszel odds ratio over days (stratified, so a good year cannot inflate it),
with a 95 % range from a bootstrap over calendar months. Only days with the full ``horizon`` of
sessions after them count (or the stock stopped trading: its outcome is final).

This module is pure (lists in, numbers out); ``superperformance_data`` loads and stores.
"""

from __future__ import annotations

import bisect
import random
import statistics
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from vcp_scanner.config.models import StageConfig
from vcp_scanner.features.weekly_stage import classify_weekly_stage

STUDY_VERSION = "superperformance-1.0.0"


@dataclass(frozen=True)
class Params:
    horizon: int = 252  # sessions a run may take from launch to peak
    min_gain: float = 0.50  # +50 %
    base_lookback: int = 65  # sessions searched for the base high (13 weeks)
    min_base: int = 10  # a breakout needs at least this many sessions since the base high
    near_top: int = 10  # warning signs: sessions either side of the peak counted as "at the top"
    after_peak: int = 60  # sessions after the peak watched for warning signs
    giveback_sessions: int = 252  # sessions after the peak searched for the low
    fundamentals_max_age: int = 200  # calendar days; an older result is "missing"
    vcp_max_age: int = 7  # calendar days between the VCP scan and the day it describes
    bootstrap: int = 300
    seed: int = 20261023


# ----------------------------------------------------------------------------- inputs


@dataclass
class Stock:
    """One stock's sessions, oldest first. ``ok`` = in the point-in-time universe that day; RS,
    Trend Template and the averages are None where the scan would have none."""

    iid: str
    symbol: str
    sector: str | None
    days: list[date]
    close: list[float]
    high: list[float]
    low: list[float]
    volume: list[float]
    ok: list[bool]
    rs: list[int | None]
    tt: list[bool | None]
    sma50: list[float | None]
    sma200: list[float | None]
    high252: list[float | None]
    vavg50: list[float | None]
    vol50: list[float | None] = field(default_factory=list)  # 50-day volatility of returns

    def __len__(self) -> int:
        return len(self.days)


@dataclass(frozen=True)
class Fundamental:
    available: date
    eps_yoy: float | None
    sales_yoy: float | None
    eps_accel: float | None


@dataclass(frozen=True)
class Holding:
    """The shareholding state from ``available`` on: one-quarter changes in percentage points."""

    available: date
    promoter_chg: float | None
    fii_chg: float | None


@dataclass
class Context:
    """Everything outside the stock's own prices, keyed for point-in-time lookups."""

    calendar: list[date]  # every session with prices, oldest first
    nifty: dict[date, float]
    market_label: dict[date, str]
    sector_tt_share: dict[tuple[str, date], float]  # sector -> share passing the Trend Template
    tt_share: dict[date, float]  # whole universe
    fundamentals: dict[str, list[Fundamental]] = field(default_factory=dict)
    holdings: dict[str, list[Holding]] = field(default_factory=dict)
    vcp: dict[str, list[tuple[date, str]]] = field(default_factory=dict)  # (scan day, class)
    eligible_count: dict[date, int] = field(default_factory=dict)
    sector_eligible: dict[tuple[str, int], int] = field(default_factory=dict)  # (sector, year)
    vol_cut: dict[date, float] = field(default_factory=dict)  # top third of 50-day volatility


# ----------------------------------------------------------------------------- helpers


def forward_max_index(values: Sequence[float], horizon: int) -> list[int | None]:
    """For each i the index of the highest value in (i, i + horizon] (the earliest on a tie)."""
    n = len(values)
    out: list[int | None] = [None] * n
    dq: deque[int] = deque()
    for i in range(n - 1, -1, -1):
        j = i + 1
        if j < n:
            while dq and values[dq[-1]] <= values[j]:
                dq.pop()
            dq.append(j)
        while dq and dq[0] > i + horizon:
            dq.popleft()
        out[i] = dq[0] if dq else None
    return out


def trailing_max(values: Sequence[float | None], window: int) -> list[float | None]:
    """Highest value over the last ``window`` entries including i (None until any value)."""
    out: list[float | None] = []
    dq: deque[int] = deque()
    for i, v in enumerate(values):
        if v is not None:
            while dq and (values[dq[-1]] or 0.0) <= v:
                dq.pop()
            dq.append(i)
        while dq and dq[0] <= i - window:
            dq.popleft()
        out.append(values[dq[0]] if dq else None)
    return out


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0


def iso_week(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return (y, w)


class WeeklyStages:
    """The weekly stage of a stock on any of its days, the way the scan classifies it: completed
    weeks plus the day's close as the partial current week (nothing after the day is read)."""

    def __init__(self, s: Stock, cfg: StageConfig) -> None:
        self.cfg = cfg
        self.need = cfg.sma_weeks + max(cfg.slope_lookback_weeks, cfg.sma_weeks)
        self.close = s.close
        self.week_closes: list[float] = []
        self.done_before: list[int] = []  # completed weeks before day i's week
        weeks = [iso_week(d) for d in s.days]
        for i, wk in enumerate(weeks):
            if i > 0 and wk != weeks[i - 1]:
                self.week_closes.append(s.close[i - 1])
            self.done_before.append(len(self.week_closes))
        self.cache: dict[int, str] = {}

    def at(self, i: int) -> str:
        if i not in self.cache:
            k = self.done_before[i]
            closes = [*self.week_closes[max(0, k - self.need) : k], self.close[i]]
            self.cache[i] = classify_weekly_stage(closes, self.cfg).stage.value
        return self.cache[i]


def weekly_bars(s: Stock, lo: int, hi: int) -> list[tuple[int, float, float]]:
    """(last index, close, volume) of each week touching sessions lo..hi, in order."""
    out: list[tuple[int, float, float]] = []
    vol = 0.0
    for i in range(lo, hi + 1):
        vol += s.volume[i]
        last = i == hi or iso_week(s.days[i + 1]) != iso_week(s.days[i])
        if last:
            out.append((i, s.close[i], vol))
            vol = 0.0
    return out


# ----------------------------------------------------------------------------- runs and bases


@dataclass(frozen=True)
class RawRun:
    launch: int
    peak: int
    complete: bool  # the full horizon after the launch is known, or the stock stopped trading


def find_runs(s: Stock, p: Params, fmax: list[int | None], ended: bool) -> list[RawRun]:
    """Non-overlapping runs of at least ``p.min_gain`` from an eligible low (module docstring).
    ``ended``: the stock stopped trading before the data ends, so every outcome is final."""
    n, c, runs, i = len(s), s.close, [], 0
    while i < n:
        hi = fmax[i]
        if not s.ok[i] or hi is None or c[hi] < (1 + p.min_gain) * c[i]:
            i += 1
            continue
        lo = min((k for k in range(i, hi) if s.ok[k]), key=lambda k: (c[k], k))
        pk = fmax[lo]
        assert pk is not None  # hi lies in (lo, lo + horizon]
        runs.append(RawRun(lo, pk, ended or lo + p.horizon <= n - 1))
        i = pk + 1
    return runs


@dataclass(frozen=True)
class Base:
    """A breakout at ``day`` (index) above the base high at ``start`` (index)."""

    day: int
    start: int
    high: float
    length: int  # sessions from the base high to the breakout
    depth_pct: float  # deepest close below the base high
    tight_pct: float | None  # high-low range of the last 10 sessions before the breakout
    dryup: float | None  # average volume of those 10 sessions / 50-day average
    volume_ratio: float | None  # breakout-day volume / 50-day average the day before
    new_high: bool  # closed at a new 52-week closing high


def find_bases(s: Stock, p: Params) -> list[Base]:
    """Every eligible breakout of the stock (module docstring), oldest first."""
    n, c = len(s), s.close
    out: list[Base] = []
    dq: deque[int] = deque()  # window [i - lookback, i - 1]; the front is the latest highest
    hi252 = trailing_max([*c], 252)
    for i in range(n):
        if i >= 1:
            j = i - 1
            while dq and c[dq[-1]] <= c[j]:
                dq.pop()
            dq.append(j)
        while dq and dq[0] < i - p.base_lookback:
            dq.popleft()
        if i < p.base_lookback or not s.ok[i] or not dq:
            continue
        h = dq[0]
        if c[i] <= c[h] or i - h < p.min_base:
            continue
        inside = c[h:i]
        hs, ls, vs = s.high[i - 10 : i], s.low[i - 10 : i], s.volume[i - 10 : i]
        top = max(hs)
        avg = s.vavg50[i - 1]
        prev_high = hi252[i - 1]
        out.append(Base(
            day=i, start=h, high=c[h], length=i - h,
            depth_pct=(1 - min(inside) / c[h]) * 100,
            tight_pct=(top - min(ls)) / top * 100 if top > 0 else None,
            dryup=statistics.fmean(vs) / avg if avg else None,
            volume_ratio=s.volume[i] / avg if avg else None,
            new_high=prev_high is not None and c[i] > prev_high,
        ))  # fmt: skip
    return out


# ----------------------------------------------------------------------------- features


@dataclass
class DayView:
    """Point-in-time lookups for one stock, built once and walked day by day."""

    s: Stock
    ctx: Context
    p: Params
    stages: WeeklyStages
    rs_line_high20: list[bool | None]
    price_high20: list[bool]
    fund_days: list[date]
    hold_days: list[date]
    vcp_days: list[date]

    @classmethod
    def build(cls, s: Stock, ctx: Context, p: Params, stage_cfg: StageConfig) -> DayView:
        line = [
            (c / ctx.nifty[d]) if ctx.nifty.get(d) else None
            for c, d in zip(s.close, s.days, strict=True)
        ]
        line_max = trailing_max(line, 252)
        at_high = [
            v is not None and m is not None and v >= m for v, m in zip(line, line_max, strict=True)
        ]
        cmax = trailing_max([*s.close], 252)
        price_at_high = [c >= (m or c) for c, m in zip(s.close, cmax, strict=True)]
        rs_hi: list[bool | None] = []
        px_hi: list[bool] = []
        last_rs = last_px = -(10**9)
        for i in range(len(s)):  # "in the last 20 sessions" = the latest high is at most 19 back
            if at_high[i]:
                last_rs = i
            if price_at_high[i]:
                last_px = i
            rs_hi.append(last_rs >= i - 19 if i >= 252 else None)
            px_hi.append(last_px >= i - 19)
        return cls(
            s, ctx, p, WeeklyStages(s, stage_cfg), rs_hi, px_hi,
            [f.available for f in ctx.fundamentals.get(s.iid, [])],
            [h.available for h in ctx.holdings.get(s.iid, [])],
            [v[0] for v in ctx.vcp.get(s.iid, [])],
        )  # fmt: skip

    def fundamental(self, i: int) -> Fundamental | None:
        d = self.s.days[i]
        k = bisect.bisect_right(self.fund_days, d) - 1
        if k < 0:
            return None
        f = self.ctx.fundamentals[self.s.iid][k]
        return f if (d - f.available).days <= self.p.fundamentals_max_age else None

    def holding(self, i: int) -> Holding | None:
        k = bisect.bisect_right(self.hold_days, self.s.days[i]) - 1
        return self.ctx.holdings[self.s.iid][k] if k >= 0 else None

    def vcp_class(self, i: int, *, before: bool) -> str | None:
        """The VCP class of the latest scan on (``before``: strictly before) day i, at most
        ``vcp_max_age`` days old; None when no scan covered the stock."""
        d = self.s.days[i]
        k = (bisect.bisect_left if before else bisect.bisect_right)(self.vcp_days, d) - 1
        if k < 0:
            return None
        day, cls = self.ctx.vcp[self.s.iid][k]
        return cls if (d - day).days <= self.p.vcp_max_age else None


Feature = Callable[[DayView, int], bool | None]


def _stage(v: DayView, i: int, *names: str) -> bool | None:
    st = v.stages.at(i)
    return None if st == "INSUFFICIENT_DATA" else st in names


def _rs(v: DayView, i: int, lo: int | None = None, hi: int | None = None) -> bool | None:
    r = v.s.rs[i]
    if r is None:
        return None
    return (lo is None or r >= lo) and (hi is None or r < hi)


def _dist(
    v: DayView, i: int, within: float | None = None, beyond: float | None = None
) -> bool | None:
    h = v.s.high252[i]
    if not h:
        return None
    below = (1 - v.s.close[i] / h) * 100
    return (within is None or below <= within) and (beyond is None or below > beyond)


def _fund(v: DayView, i: int, key: str, cut: float) -> bool | None:
    f = v.fundamental(i)
    val = None if f is None else getattr(f, key)
    return None if val is None else val >= cut


def _both_growth(v: DayView, i: int) -> bool | None:
    f = v.fundamental(i)
    if f is None or f.eps_yoy is None or f.sales_yoy is None:
        return None
    return f.eps_yoy >= 25 and f.sales_yoy >= 20


def _hold(v: DayView, i: int, key: str) -> bool | None:
    h = v.holding(i)
    val = None if h is None else getattr(h, key)
    return None if val is None else val > 0


def _rs_led(v: DayView, i: int) -> bool | None:
    hi = v.rs_line_high20[i]
    return None if hi is None else hi and not v.price_high20[i]


def _sector_leading(v: DayView, i: int) -> bool | None:
    sec, d = v.s.sector, v.s.days[i]
    if sec is None:
        return None
    share, overall = v.ctx.sector_tt_share.get((sec, d)), v.ctx.tt_share.get(d)
    return None if share is None or overall is None else share > overall


def _vcp(v: DayView, i: int, *classes: str) -> bool | None:
    cls = v.vcp_class(i, before=True)
    return None if cls is None else cls in classes


def _tt(v: DayView, i: int) -> bool | None:
    return v.s.tt[i]


def _volatile(v: DayView, i: int) -> bool | None:
    """In the most volatile third of the eligible stocks that day (50-day volatility)."""
    vol = v.s.vol50[i] if i < len(v.s.vol50) else None
    cut = v.ctx.vol_cut.get(v.s.days[i])
    return None if vol is None or cut is None else vol >= cut


def _above200(v: DayView, i: int) -> bool | None:
    m = v.s.sma200[i]
    return None if m is None else v.s.close[i] > m


#: (key, plain label, test) measured on the launch day.
LAUNCH_FEATURES: list[tuple[str, str, Feature]] = [
    ("stage1", "Weekly Stage 1 (basing)", lambda v, i: _stage(v, i, "STAGE_1")),
    ("stage2", "Weekly Stage 2 (advancing)", lambda v, i: _stage(v, i, "STAGE_2")),
    ("stage4", "Weekly Stage 4 (declining)", lambda v, i: _stage(v, i, "STAGE_4")),
    ("tt", "Trend Template passed", _tt),
    ("above200", "Close above the 200-day average", _above200),
    ("rs90", "RS rank 90+", lambda v, i: _rs(v, i, 90)),
    ("rs80", "RS rank 80+", lambda v, i: _rs(v, i, 80)),
    ("rs_lt50", "RS rank below 50", lambda v, i: _rs(v, i, None, 50)),
    ("rs_line_high", "RS line at a 52-week high in the last 20 sessions",
     lambda v, i: v.rs_line_high20[i]),
    ("rs_led", "RS line at a 52-week high, price not (RS led price)", _rs_led),
    ("near_high", "Within 15 % of the 52-week high", lambda v, i: _dist(v, i, within=15)),
    ("far_high", "More than 40 % below the 52-week high", lambda v, i: _dist(v, i, beyond=40)),
    ("eps25", "Latest quarter EPS up 25 %+ on a year", lambda v, i: _fund(v, i, "eps_yoy", 25)),
    ("sales20", "Latest quarter sales up 20 %+ on a year",
     lambda v, i: _fund(v, i, "sales_yoy", 20)),
    ("eps_sales", "EPS up 25 %+ and sales up 20 %+", _both_growth),
    ("eps_accel", "EPS growth speeding up", lambda v, i: _fund(v, i, "eps_accel", 1e-9)),
    ("promoter_up", "Promoter holding up on the quarter",
     lambda v, i: _hold(v, i, "promoter_chg")),
    ("fii_up", "FII holding up on the quarter", lambda v, i: _hold(v, i, "fii_chg")),
    ("sector_leading", "Sector's Trend Template share above the market's", _sector_leading),
    ("volatile", "Most volatile third of stocks that day (50-day)", _volatile),
]  # fmt: skip

#: (key, plain label, test) measured on the breakout day; the base is passed through ``BASE``.
BASE_FEATURES: list[tuple[str, str, Callable[[DayView, Base], bool | None]]] = [
    ("vcp", "Our VCP detector flagged it (VCP / A+ VCP)",
     lambda v, b: _vcp(v, b.day, "VCP", "A_PLUS_VCP")),
    ("vcp_like", "VCP-like in the detector", lambda v, b: _vcp(v, b.day, "VCP_LIKE")),
    ("base_short", "Base 10-24 sessions", lambda v, b: b.length < 25),
    ("base_long", "Base 45+ sessions", lambda v, b: b.length >= 45),
    ("shallow", "Base depth under 15 %", lambda v, b: b.depth_pct < 15),
    ("deep", "Base depth 30 %+", lambda v, b: b.depth_pct >= 30),
    ("tight", "Last 10 sessions within 10 % (tight)",
     lambda v, b: None if b.tight_pct is None else b.tight_pct <= 10),
    ("dryup", "Volume dried up (last 10 sessions under 75 % of average)",
     lambda v, b: None if b.dryup is None else b.dryup < 0.75),
    ("bo_volume", "Breakout volume 1.5x average or more",
     lambda v, b: None if b.volume_ratio is None else b.volume_ratio >= 1.5),
    ("new_high", "Breakout to a new 52-week closing high", lambda v, b: b.new_high),
    ("tt", "Trend Template passed", lambda v, b: v.s.tt[b.day]),
    ("stage2", "Weekly Stage 2", lambda v, b: _stage(v, b.day, "STAGE_2")),
    ("rs90", "RS rank 90+", lambda v, b: _rs(v, b.day, 90)),
    ("rs_led", "RS line led price", lambda v, b: _rs_led(v, b.day)),
    ("eps25", "Latest quarter EPS up 25 %+", lambda v, b: _fund(v, b.day, "eps_yoy", 25)),
    ("eps_accel", "EPS growth speeding up", lambda v, b: _fund(v, b.day, "eps_accel", 1e-9)),
    ("sector_leading", "Sector's Trend Template share above the market's",
     lambda v, b: _sector_leading(v, b.day)),
    ("volatile", "Most volatile third of stocks that day (50-day)",
     lambda v, b: _volatile(v, b.day)),
]  # fmt: skip


# ----------------------------------------------------------------------------- one run


@dataclass
class Run:
    """One 50 %+ run with its measurements; dates, prices and percentages, never indices."""

    iid: str
    symbol: str
    sector: str | None
    launch_date: date
    launch_close: float
    peak_date: date
    peak_close: float
    gain_pct: float
    sessions_to_peak: int
    complete: bool
    to20: int | None
    to50: int | None
    to100: int | None
    max_pullback_pct: float
    pullbacks: list[float]  # every pullback of 5 %+ from a high inside the run
    bases: list[Base]  # breakouts from the launch to the peak, oldest first
    breakout_gain_pct: float | None  # first breakout close above the launch close
    launch: dict[str, Any]  # launch-day features (stage, RS, Trend Template, fundamentals ...)
    vcp_before_breakout: str | None
    pct_above_50: float | None
    closes_below_50: int | None
    weeks_above_10w_pct: float | None
    rs90_share: float | None
    updown_volume: float | None
    climax_week: date | None
    climax_gain_pct: float | None
    climax_near_top: bool | None
    heavy_break_sessions: int | None  # first heavy-volume close below the 50-day after the peak
    heavy_break_drawdown_pct: float | None
    rs_rollover_sessions: int | None
    stage34_sessions: int | None
    stage34: str | None
    giveback_pct: float | None  # deepest close below the peak in the sessions after it
    giveback_of_gain_pct: float | None
    giveback_final: bool  # the whole give-back window is known
    sector_peers: int = 0  # other runs of the same sector launched within 30 days
    path: list[float] = field(default_factory=list)  # close / launch close, sessions 0..horizon
    top_path: list[float | None] = field(default_factory=list)  # close / peak, -60..+120
    case: dict[str, Any] | None = None  # chart series for the biggest runs (case studies)
    base_dates: list[tuple[date, date]] = field(default_factory=list)  # (base high, breakout)

    @property
    def doubled(self) -> bool:
        return self.gain_pct >= 100

    @property
    def tripled(self) -> bool:
        return self.gain_pct >= 200

    def date_of(self, s: Stock, i: int) -> date:
        return s.days[i]


def _pullbacks(c: Sequence[float], lo: int, pk: int, min_pct: float = 5.0) -> list[float]:
    out, top, deepest = [], c[lo], 0.0
    for i in range(lo + 1, pk + 1):
        if c[i] > top:
            if deepest >= min_pct:
                out.append(deepest)
            top, deepest = c[i], 0.0
        else:
            deepest = max(deepest, (1 - c[i] / top) * 100)
    if deepest >= min_pct:
        out.append(deepest)
    return out


def _first_at(c: Sequence[float], lo: int, pk: int, level: float) -> int | None:
    return next((i - lo for i in range(lo, pk + 1) if c[i] >= level), None)


def _weekly_measures(
    s: Stock, lo: int, pk: int
) -> tuple[float | None, float | None, date | None, float | None]:
    """(share of weeks closing above the 10-week line, up-week / down-week volume, the week of
    the biggest weekly gain and that gain) between the launch and the peak."""
    bars = weekly_bars(s, max(0, lo - 60), pk)
    closes = [b[1] for b in bars]
    above = weeks = 0
    up = down = 0.0
    best: tuple[float, date] | None = None
    for k, (end, close, vol) in enumerate(bars):
        if end < lo or k == 0:
            continue
        if k >= 9:
            weeks += 1
            above += close > statistics.fmean(closes[k - 9 : k + 1])
        prev = closes[k - 1]
        if close > prev:
            up += vol
        elif close < prev:
            down += vol
        g = _pct(close, prev)
        if best is None or g > best[0]:
            best = (g, s.days[end])
    return (
        above / weeks * 100 if weeks else None,
        up / down if down else None,
        best[1] if best else None,
        best[0] if best else None,
    )


def measure_run(
    s: Stock, r: RawRun, p: Params, view: DayView, bases: list[Base], ended: bool
) -> Run:
    c, lo, pk, n = s.close, r.launch, r.peak, len(s)
    mine = [b for b in bases if lo <= b.day <= pk]
    first = mine[0] if mine else None
    start50 = next((i for i in range(lo, pk + 1)
                    if s.sma50[i] is not None and c[i] > (s.sma50[i] or 0)), None)  # fmt: skip
    above50 = below50 = 0
    if start50 is not None:
        for i in range(start50, pk + 1):
            m = s.sma50[i]
            if m is not None:
                above50 += c[i] >= m
                below50 += c[i] < m
    from_i = first.day if first else lo
    rs_vals = [x for x in s.rs[from_i : pk + 1] if x is not None]
    w10, updown, climax, climax_gain = _weekly_measures(s, lo, pk)
    climax_near = None
    if climax is not None:
        climax_near = (s.days.index(climax) >= pk - 15) if climax in s.days else None

    after = range(pk + 1, min(n, pk + 1 + p.giveback_sessions))
    heavy = next(
        (
            j
            for j in after
            if s.sma50[j] is not None
            and c[j] < (s.sma50[j] or 0)
            and s.vavg50[j - 1]
            and s.volume[j] >= 1.5 * (s.vavg50[j - 1] or 0)
        ),
        None,
    )
    had90 = any(x >= 90 for x in s.rs[lo : pk + 1] if x is not None)
    roll = next((j for j in after if s.rs[j] is not None and (s.rs[j] or 0) < 70), None)
    st34 = next((j for j in after if view.stages.at(j) in ("STAGE_3", "STAGE_4")), None)
    low_after = min((c[j] for j in after), default=None)

    i = lo
    f = view.fundamental(i)
    h = view.holding(i)
    hi252 = s.high252[i]
    launch = {
        "stage": view.stages.at(i), "rs": s.rs[i], "tt": s.tt[i],
        "dist_high_pct": (1 - c[i] / hi252) * 100 if hi252 else None,
        "rs_led": _rs_led(view, i),
        "eps_yoy": f.eps_yoy if f else None, "sales_yoy": f.sales_yoy if f else None,
        "eps_accel": f.eps_accel if f else None,
        "promoter_chg": h.promoter_chg if h else None, "fii_chg": h.fii_chg if h else None,
        "market": view.ctx.market_label.get(s.days[i]),
    }  # fmt: skip
    path = [c[k] / c[lo] for k in range(lo, min(n, lo + p.horizon + 1))]
    top_path = [c[k] / c[pk] if 0 <= k < n else None for k in range(pk - 60, pk + 121)]
    final = ended or pk + p.giveback_sessions <= n - 1
    return Run(
        iid=s.iid, symbol=s.symbol, sector=s.sector,
        launch_date=s.days[lo], launch_close=c[lo], peak_date=s.days[pk], peak_close=c[pk],
        gain_pct=_pct(c[pk], c[lo]), sessions_to_peak=pk - lo, complete=r.complete,
        to20=_first_at(c, lo, pk, 1.2 * c[lo]), to50=_first_at(c, lo, pk, 1.5 * c[lo]),
        to100=_first_at(c, lo, pk, 2.0 * c[lo]),
        max_pullback_pct=max(_pullbacks(c, lo, pk, 0.0), default=0.0),
        pullbacks=_pullbacks(c, lo, pk),
        bases=mine, breakout_gain_pct=_pct(c[first.day], c[lo]) if first else None,
        launch=launch,
        vcp_before_breakout=view.vcp_class(first.day, before=True) if first else None,
        pct_above_50=above50 / (above50 + below50) * 100 if above50 + below50 else None,
        closes_below_50=below50 if start50 is not None else None,
        weeks_above_10w_pct=w10,
        rs90_share=sum(x >= 90 for x in rs_vals) / len(rs_vals) * 100 if rs_vals else None,
        updown_volume=updown, climax_week=climax, climax_gain_pct=climax_gain,
        climax_near_top=climax_near,
        heavy_break_sessions=None if heavy is None else heavy - pk,
        heavy_break_drawdown_pct=None if heavy is None else (1 - c[heavy] / c[pk]) * 100,
        rs_rollover_sessions=None if roll is None or not had90 else roll - pk,
        stage34_sessions=None if st34 is None else st34 - pk,
        stage34=None if st34 is None else view.stages.at(st34),
        giveback_pct=None if low_after is None else max(0.0, (1 - low_after / c[pk]) * 100),
        giveback_of_gain_pct=None if low_after is None else max(0.0, (c[pk] - low_after)
                                                                / (c[pk] - c[lo]) * 100),
        giveback_final=final, path=path, top_path=top_path,
        base_dates=[(s.days[b.start], s.days[b.day]) for b in mine],
    )  # fmt: skip


# ----------------------------------------------------------------------------- warning signs

#: (key, plain label). Each is an event on one session, known that day.
WARNINGS: list[tuple[str, str]] = [
    ("climax_week", "Biggest 5-session gain of the run so far (15 %+)"),
    ("biggest_day", "Biggest one-day gain of the run so far (5 %+)"),
    ("extended_50", "Close 25 %+ above the 50-day average"),
    ("heavy_down", "Down day on twice the average volume"),
    ("heavy_below_50", "Close below the 50-day on 1.5x average volume"),
    ("rs_rollover", "RS rank falls below 80 after 90+ in the run"),
    ("stage34", "Weekly stage turns from 2 to 3 or 4"),
    ("below_10w", "Week closes below the 10-week line after 4+ weeks above"),
]


@dataclass
class WarningTally:
    sessions: int = 0
    near_sessions: int = 0
    events: Counter[str] = field(default_factory=Counter)
    near_events: Counter[str] = field(default_factory=Counter)
    runs_near: Counter[str] = field(default_factory=Counter)  # runs with the sign at the top
    runs: int = 0
    offsets: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))


def tally_warnings(s: Stock, r: RawRun, p: Params, view: DayView, t: WarningTally) -> None:
    """Count each warning sign over the run and the sessions after its peak, split by whether
    the session is within ``p.near_top`` of the peak."""
    c, lo, pk = s.close, r.launch, r.peak
    end = min(len(s) - 1, pk + p.after_peak)
    best5 = best1 = 0.0
    max_rs = 0
    week_ends = {b[0] for b in weekly_bars(s, max(0, lo - 60), end)}
    wk_closes: list[float] = [s.close[k] for k in sorted(week_ends) if k < lo]
    weeks_above = 0
    seen: set[str] = set()
    t.runs += 1
    for i in range(lo + 1, end + 1):
        ev: list[str] = []
        g1 = _pct(c[i], c[i - 1])
        if g1 > best1:
            best1 = g1
            if g1 >= 5:
                ev.append("biggest_day")
        if i - lo >= 5:
            g5 = _pct(c[i], c[i - 5])
            if g5 > best5:
                best5 = g5
                if g5 >= 15:
                    ev.append("climax_week")
        m50, avg = s.sma50[i], s.vavg50[i - 1]
        if (
            m50
            and c[i] >= 1.25 * m50
            and not (s.sma50[i - 1] and c[i - 1] >= 1.25 * (s.sma50[i - 1] or 0))
        ):
            ev.append("extended_50")
        if avg and c[i] < c[i - 1] and s.volume[i] >= 2 * avg:
            ev.append("heavy_down")
        if avg and m50 and c[i] < m50 and s.volume[i] >= 1.5 * avg:
            ev.append("heavy_below_50")
        rs, rs_prev = s.rs[i], s.rs[i - 1]
        if rs is not None and rs_prev is not None:
            if max_rs >= 90 and rs < 80 <= rs_prev:
                ev.append("rs_rollover")
            max_rs = max(max_rs, rs)
        if view.stages.at(i) in ("STAGE_3", "STAGE_4") and view.stages.at(i - 1) == "STAGE_2":
            ev.append("stage34")
        if i in week_ends:
            wk_closes.append(c[i])
            if len(wk_closes) >= 10:
                if c[i] < statistics.fmean(wk_closes[-10:]):
                    if weeks_above >= 4:
                        ev.append("below_10w")
                    weeks_above = 0
                else:
                    weeks_above += 1
        near = abs(i - pk) <= p.near_top
        t.sessions += 1
        t.near_sessions += near
        for e in ev:
            t.events[e] += 1
            t.offsets[e].append(i - pk)
            if near:
                t.near_events[e] += 1
                if e not in seen:
                    t.runs_near[e] += 1
                    seen.add(e)


# ----------------------------------------------------------------------------- lift


@dataclass
class LiftCounts:
    """Per feature and day: [winners with it, winners without, rest with, rest without]."""

    cells: dict[str, dict[date, list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    )
    winners: Counter[date] = field(default_factory=Counter)
    rest: Counter[date] = field(default_factory=Counter)
    #: the same counts per day and volatility third (top third or not): lift with volatility
    #: held equal, since a fixed +50 % bar is easier for a volatile stock to clear
    adj: dict[str, dict[tuple[date, bool], list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    )

    def add(
        self, d: date, winner: bool, values: Iterable[tuple[str, bool | None]],
        volatile: bool | None = None,
    ) -> None:  # fmt: skip
        (self.winners if winner else self.rest)[d] += 1
        for key, val in values:
            if val is None:
                continue
            cell = (0 if winner else 2) + (0 if val else 1)
            self.cells[key][d][cell] += 1
            if volatile is not None:
                self.adj[key][(d, volatile)][cell] += 1


def mantel_haenszel(cells: Iterable[Sequence[int]]) -> float | None:
    num = den = 0.0
    for a, b, c, d in cells:
        n = a + b + c + d
        if n:
            num += a * d / n
            den += b * c / n
    return num / den if den else None


def lift_table(
    lc: LiftCounts, features: Sequence[tuple[str, str]], p: Params, days: set[date] | None = None
) -> list[dict[str, Any]]:
    """One row per feature: shares among winners and the rest on days with a winner, the
    stratified lift and its bootstrap range (blocks = calendar months)."""
    rng = random.Random(p.seed)
    out = []
    for key, label in features:
        per_day = {d: v for d, v in lc.cells.get(key, {}).items()
                   if lc.winners[d] and (days is None or d in days)}  # fmt: skip
        if not per_day:
            continue
        a = sum(v[0] for v in per_day.values())
        b = sum(v[1] for v in per_day.values())
        c = sum(v[2] for v in per_day.values())
        d_ = sum(v[3] for v in per_day.values())
        months: dict[tuple[int, int], list[list[int]]] = defaultdict(list)
        for day, v in per_day.items():
            months[(day.year, day.month)].append(v)
        blocks = list(months.values())
        boots: list[float] = []
        for _ in range(p.bootstrap):
            pick = [cell for _k in blocks for cell in rng.choice(blocks)]
            val = mantel_haenszel(pick)
            if val is not None:
                boots.append(val)
        boots.sort()
        lo_hi = (boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]) if len(
            boots) >= 40 else (None, None)  # fmt: skip
        out.append({
            "key": key, "label": label,
            "winners": a + b, "winners_with": a, "rest": c + d_, "rest_with": c,
            "share_winners": a / (a + b) * 100 if a + b else None,
            "share_rest": c / (c + d_) * 100 if c + d_ else None,
            "lift": mantel_haenszel(per_day.values()), "lift_lo": lo_hi[0], "lift_hi": lo_hi[1],
            "lift_vol_adj": mantel_haenszel(lc.adj.get(key, {}).values()),
        })  # fmt: skip
    return out


# ----------------------------------------------------------------------------- the study


@dataclass
class StudyResult:
    runs: list[Run] = field(default_factory=list)
    launch: LiftCounts = field(default_factory=LiftCounts)
    breakout: LiftCounts = field(default_factory=LiftCounts)
    warnings: WarningTally = field(default_factory=WarningTally)
    # day -> [eligible stocks, launches, breakouts, breakout winners]
    by_day: dict[date, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0, 0, 0]))
    stocks: int = 0


def lift_end_day(calendar: Sequence[date], horizon: int) -> date:
    """The last day with ``horizon`` sessions after it in the data."""
    return calendar[-horizon - 1] if len(calendar) > horizon else calendar[0]


def study_stock(
    s: Stock, ctx: Context, p: Params, stage_cfg: StageConfig, res: StudyResult
) -> None:
    """Add one stock's runs, breakouts and lift rows to ``res``."""
    if len(s) < 2:
        return
    res.stocks += 1
    data_end, lift_end = ctx.calendar[-1], lift_end_day(ctx.calendar, p.horizon)
    ended = s.days[-1] < data_end
    c = s.close
    fmax = forward_max_index(c, p.horizon)
    raw = find_runs(s, p, fmax, ended)
    bases = find_bases(s, p)
    view = DayView.build(s, ctx, p, stage_cfg)
    launches = {r.launch for r in raw}
    for i in range(len(s)):
        d = s.days[i]
        if not s.ok[i] or d > lift_end:
            continue
        won = i in launches
        res.launch.add(d, won, ((k, f(view, i)) for k, _l, f in LAUNCH_FEATURES),
                       _volatile(view, i))  # fmt: skip
        day = res.by_day[d]
        day[0] += 1
        day[1] += won
    for b in bases:
        d = s.days[b.day]
        if d > lift_end:
            continue
        hi = fmax[b.day]
        won = hi is not None and c[hi] >= (1 + p.min_gain) * c[b.day]
        res.breakout.add(d, won, ((k, f(view, b)) for k, _l, f in BASE_FEATURES),
                         _volatile(view, b.day))  # fmt: skip
        res.by_day[d][2] += 1
        res.by_day[d][3] += won
    for r in raw:
        run = measure_run(s, r, p, view, bases, ended)
        if r.complete and run.gain_pct >= CASE_MIN_GAIN:
            run.case = case_payload(s, run, r)
        res.runs.append(run)
        if r.complete and (ended or r.peak + p.after_peak <= len(s) - 1):
            tally_warnings(s, r, p, view, res.warnings)


def add_sector_peers(runs: list[Run], days: int = 30) -> None:
    by_sector: dict[str, list[Run]] = defaultdict(list)
    for r in runs:
        if r.sector:
            by_sector[r.sector].append(r)
    for group in by_sector.values():
        group.sort(key=lambda r: r.launch_date)
        starts = [r.launch_date for r in group]
        for r in group:
            lo = bisect.bisect_left(starts, r.launch_date - timedelta(days=days))
            hi = bisect.bisect_right(starts, r.launch_date + timedelta(days=days))
            r.sector_peers = hi - lo - 1


# ----------------------------------------------------------------------------- summaries


def _median(xs: Iterable[float | int | None]) -> float | None:
    vals = [float(x) for x in xs if x is not None]
    return statistics.median(vals) if vals else None


def _share(xs: Iterable[bool | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) * 100 if vals else None


def _quantiles(vals: list[float]) -> tuple[float, float, float] | None:
    if len(vals) < 5:
        return None
    q = statistics.quantiles(vals, n=4)
    return q[0], q[1], q[2]


def gain_band(gain_pct: float) -> str:
    return "tripled" if gain_pct >= 200 else "doubled" if gain_pct >= 100 else "50-100"


def _during(runs: list[Run]) -> dict[str, Any]:
    return {
        "runs": len(runs),
        "gain_pct": _median(r.gain_pct for r in runs),
        "sessions_to_peak": _median(r.sessions_to_peak for r in runs),
        "to20": _median(r.to20 for r in runs), "to50": _median(r.to50 for r in runs),
        "to100": _median(r.to100 for r in runs),
        "max_pullback_pct": _median(r.max_pullback_pct for r in runs),
        "pullbacks_10": _median(sum(x >= 10 for x in r.pullbacks) for r in runs),
        "pct_above_50": _median(r.pct_above_50 for r in runs),
        "never_below_50": _share(None if r.closes_below_50 is None else r.closes_below_50 == 0
                                 for r in runs),
        "weeks_above_10w_pct": _median(r.weeks_above_10w_pct for r in runs),
        "bases": _median(max(0, len(r.bases) - 1) for r in runs),
        "rs90_share": _median(r.rs90_share for r in runs),
        "updown_volume": _median(r.updown_volume for r in runs),
        "breakout_gain_pct": _median(r.breakout_gain_pct for r in runs),
    }  # fmt: skip


def _end(runs: list[Run]) -> dict[str, Any]:
    final = [r for r in runs if r.giveback_final]
    return {
        "runs": len(runs), "runs_final": len(final),
        "climax_near_top": _share(r.climax_near_top for r in runs),
        "climax_gain_pct": _median(r.climax_gain_pct for r in runs),
        "heavy_break_sessions": _median(r.heavy_break_sessions for r in runs),
        "heavy_break_drawdown_pct": _median(r.heavy_break_drawdown_pct for r in runs),
        "rs_rollover_sessions": _median(r.rs_rollover_sessions for r in runs),
        "stage34_sessions": _median(r.stage34_sessions for r in runs),
        "giveback_pct": _median(r.giveback_pct for r in final),
        "giveback_of_gain_pct": _median(r.giveback_of_gain_pct for r in final),
        "gave_back_half": _share(None if r.giveback_of_gain_pct is None
                                 else r.giveback_of_gain_pct >= 50 for r in final),
        "gave_back_all": _share(None if r.giveback_of_gain_pct is None
                                else r.giveback_of_gain_pct >= 100 for r in final),
    }  # fmt: skip


def _paths(runs: list[Run], p: Params) -> dict[str, list[dict[str, float]]]:
    done = [r for r in runs if r.complete]
    launch = []
    for k in range(0, p.horizon + 1):
        vals = [r.path[k] for r in done if len(r.path) > k]
        q = _quantiles(vals)
        if q:
            launch.append({"k": k, "q25": q[0], "q50": q[1], "q75": q[2], "n": len(vals)})
    top = []
    final = [r for r in runs if r.giveback_final]
    for k in range(-60, 121):
        vals = [v for r in final if (v := r.top_path[k + 60]) is not None]
        q = _quantiles(vals)
        if q:
            top.append({"k": k, "q25": q[0], "q50": q[1], "q75": q[2], "n": len(vals)})
    return {"launch": launch, "top": top}


PULLBACK_BINS = [(5, 10), (10, 15), (15, 20), (20, 25), (25, 30), (30, 40), (40, 1000)]


def _pullback_hist(runs: list[Run]) -> dict[str, Any]:
    def bins(vals: list[float]) -> list[dict[str, Any]]:
        return [{"lo": lo, "hi": hi, "n": sum(lo <= v < hi for v in vals)}
                for lo, hi in PULLBACK_BINS]  # fmt: skip

    done = [r for r in runs if r.complete]
    return {
        "max_per_run": bins([r.max_pullback_pct for r in done]),
        "max_under_5": sum(r.max_pullback_pct < 5 for r in done),
        "every_pullback": bins([x for r in done for x in r.pullbacks]),
        "runs": len(done),
        "by_band": {band: _median(r.max_pullback_pct for r in done if gain_band(r.gain_pct)
                                  == band) for band in ("50-100", "doubled", "tripled")},
    }  # fmt: skip


def _warnings(t: WarningTally) -> list[dict[str, Any]]:
    base = t.near_sessions / t.sessions if t.sessions else None
    out = []
    for key, label in WARNINGS:
        n, near = t.events[key], t.near_events[key]
        share = near / n if n else None
        out.append({
            "key": key, "label": label, "events": n, "near_top": near,
            "share_near_top": None if share is None else share * 100,
            "lift": None if share is None or not base else share / base,
            "runs_with_sign_at_top": t.runs_near[key] / t.runs * 100 if t.runs else None,
            "median_offset": _median(t.offsets[key]),
        })  # fmt: skip
    return out


MARKET_ORDER = ["Strong", "Constructive", "Mixed", "Weak", "Adverse", "No reading"]


def nifty_trend(ctx: Context) -> dict[date, str]:
    """Each day: NIFTY 500 above or below its 200-day average (closes up to that day)."""
    days = sorted(ctx.nifty)
    out: dict[date, str] = {}
    window: deque[float] = deque()
    total = 0.0
    for d in days:
        window.append(ctx.nifty[d])
        total += ctx.nifty[d]
        if len(window) > 200:
            total -= window.popleft()
        if len(window) == 200:
            out[d] = "NIFTY 500 above its 200-day" if ctx.nifty[d] > total / 200 else (
                "NIFTY 500 below its 200-day")  # fmt: skip
    return out


def _market(res: StudyResult, label_of: dict[date, str], order: list[str]) -> list[dict[str, Any]]:
    """Launch rate per 1,000 eligible stock-days and breakout win rate, by a day-level reading
    (the same for every stock on a day, so pooled rather than stratified)."""
    groups: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    for d, (elig, wins, bos, bo_wins) in res.by_day.items():
        g = groups[label_of.get(d) or "No reading"]
        g[0] += elig
        g[1] += wins
        g[2] += bos
        g[3] += bo_wins
        g[4] += 1
    tot = [sum(g[k] for g in groups.values()) for k in range(4)]
    base = tot[1] / tot[0] if tot[0] else None
    bo_base = tot[3] / tot[2] if tot[2] else None
    out = []
    for label in sorted(groups, key=lambda x: order.index(x) if x in order else 99):
        e, w, b, bw, days = groups[label]
        rate = w / e if e else None
        bo_rate = bw / b if b else None
        out.append({
            "label": label, "days": days, "stock_days": e, "launches": w,
            "per_1000": None if rate is None else rate * 1000,
            "lift": None if rate is None or not base else rate / base,
            "breakouts": b, "breakout_winners": bw,
            "breakout_win_pct": None if bo_rate is None else bo_rate * 100,
            "breakout_lift": None if bo_rate is None or not bo_base else bo_rate / bo_base,
        })  # fmt: skip
    return out


def _sectors(runs: list[Run], ctx: Context) -> dict[str, Any]:
    years = sorted({r.launch_date.year for r in runs})
    cells: Counter[tuple[str, int]] = Counter((r.sector or "Unclassified", r.launch_date.year)
                                              for r in runs)  # fmt: skip
    rows: list[dict[str, Any]] = []
    for sec in sorted({k[0] for k in cells}):
        counts = {str(y): cells[(sec, y)] for y in years}
        stock_years = sum(ctx.sector_eligible.get((sec, y), 0) for y in years)
        rows.append({
            "sector": sec, "runs": sum(counts.values()), "by_year": counts,
            "stock_years": stock_years,
            "per_100": sum(counts.values()) / stock_years * 100 if stock_years else None,
            "peers_median": _median(r.sector_peers for r in runs
                                    if (r.sector or "Unclassified") == sec),
        })  # fmt: skip
    rows.sort(key=lambda r: -int(r["runs"]))
    by_year = [{"year": y, "runs": sum(1 for r in runs if r.launch_date.year == y),
                "doubled": sum(1 for r in runs if r.launch_date.year == y and r.doubled),
                "tripled": sum(1 for r in runs if r.launch_date.year == y and r.tripled)}
               for y in years]  # fmt: skip
    return {"years": years, "rows": rows, "by_year": by_year}


CASE_MIN_GAIN = 150.0  # runs kept as case-study candidates (the chart series is stored)


def case_payload(s: Stock, r: Run, raw: RawRun) -> dict[str, Any]:
    """What a case-study chart needs: closes and the 50-day from 40 sessions before the launch
    to 60 after the peak, with the bases along the way."""
    a, b = max(0, raw.launch - 40), min(len(s) - 1, raw.peak + 60)
    return {
        "symbol": r.symbol, "sector": r.sector, "launch": r.launch_date.isoformat(),
        "launch_close": r.launch_close, "peak": r.peak_date.isoformat(),
        "peak_close": r.peak_close, "gain_pct": r.gain_pct,
        "sessions": r.sessions_to_peak, "max_pullback_pct": r.max_pullback_pct,
        "launch_features": dict(r.launch),
        "vcp_before_breakout": r.vcp_before_breakout,
        "bases": [{"breakout": s.days[x.day].isoformat(), "start": s.days[x.start].isoformat(),
                   "high": x.high, "depth_pct": x.depth_pct, "length": x.length}
                  for x in r.bases],
        "series": [{"d": s.days[k].isoformat(), "c": round(s.close[k], 2),
                    "m50": None if s.sma50[k] is None else round(s.sma50[k] or 0.0, 2)}
                   for k in range(a, b + 1)],
        "giveback_pct": r.giveback_pct, "heavy_break_sessions": r.heavy_break_sessions,
    }  # fmt: skip


def case_studies(runs: list[Run], count: int = 12) -> list[dict[str, Any]]:
    """The biggest leaders: the largest complete run of each stock, biggest first."""
    best: dict[str, Run] = {}
    for r in runs:
        if r.case is not None and (r.iid not in best or r.gain_pct > best[r.iid].gain_pct):
            best[r.iid] = r
    return [
        r.case
        for r in sorted(best.values(), key=lambda r: -r.gain_pct)[:count]
        if r.case is not None
    ]


def summarize(res: StudyResult, ctx: Context, p: Params) -> dict[str, Any]:
    runs = res.runs
    lift_end = lift_end_day(ctx.calendar, p.horizon)
    done = [r for r in runs if r.complete]
    bands = {b: [r for r in done if gain_band(r.gain_pct) == b]
             for b in ("50-100", "doubled", "tripled")}  # fmt: skip
    first_ok = min((d for d, v in res.by_day.items() if v[0]), default=None)
    return {
        "version": STUDY_VERSION,
        "params": p.__dict__,
        "data_end": ctx.calendar[-1].isoformat(),
        "lift_from": None if first_ok is None else first_ok.isoformat(),
        "lift_end": lift_end.isoformat(),
        "counts": {
            "runs": len(runs),
            "stocks": len({r.iid for r in runs}),
            "stocks_studied": res.stocks,
            "complete": len(done),
            "open": len(runs) - len(done),
            "doubled": sum(r.doubled for r in runs),
            "tripled": sum(r.tripled for r in runs),
            "launch_days": sum(1 for v in res.by_day.values() if v[1]),
            "eligible_stock_days": sum(v[0] for v in res.by_day.values()),
            "breakouts": sum(v[2] for v in res.by_day.values()),
            "breakout_winners": sum(v[3] for v in res.by_day.values()),
        },  # fmt: skip
        "lift_launch": lift_table(res.launch, [(k, lbl) for k, lbl, _f in LAUNCH_FEATURES], p),
        "lift_breakout": lift_table(res.breakout, [(k, lbl) for k, lbl, _f in BASE_FEATURES], p),
        "market": _market(res, ctx.market_label, MARKET_ORDER),
        "index_trend": _market(
            res, nifty_trend(ctx), ["NIFTY 500 above its 200-day", "NIFTY 500 below its 200-day"]
        ),
        "during": {"all": _during(done), **{b: _during(rs) for b, rs in bands.items()}},
        "end": {"all": _end(done), **{b: _end(rs) for b, rs in bands.items()}},
        "paths": _paths(runs, p),
        "pullbacks": _pullback_hist(runs),
        "warnings": _warnings(res.warnings),
        "warning_runs": res.warnings.runs,
        "sectors": _sectors(runs, ctx),
        "vcp_before_breakout": dict(Counter(r.vcp_before_breakout or "no scan" for r in done)),
        "cases": case_studies(runs),
    }
