"""Scoring of one day: features in, pillar scores, raw score, label, caps and the shadow traffic
light out. Pure functions, no I/O (MARKET_HEALTH_SPECIFICATION §4-§6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from vcp_scanner.market_health.settings import LABEL_ORDER, PILLARS, HealthSettings, Label, Rung


def ladder(x: float, points: Sequence[tuple[float, float]]) -> float:
    """Points for reading ``x``: a straight line between neighbouring ladder points, held flat
    outside them."""
    if x <= points[0][0]:
        return float(points[0][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x <= x1:
            return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return float(points[-1][1])


@dataclass(frozen=True)
class DayFeatures:
    """Every reading the score uses on one day; None when it cannot be measured."""

    # A. NIFTY 500 and its confirmations
    n500_close: float | None = None
    n500_sma50: float | None = None
    n500_sma150: float | None = None
    n500_sma200: float | None = None
    n500_sma200_prior: float | None = None
    n500_high_252: float | None = None
    nifty50_above_50: bool | None = None
    smallcap_above_50: bool | None = None
    # B. breadth of our universe
    universe: int = 0
    pct_above_50: float | None = None
    pct_above_150: float | None = None
    pct_above_200: float | None = None
    ad_ratio: float | None = None
    highs_lows: float | None = None
    above_50_change: float | None = None
    vqi_gap: float | None = None
    ad_line_gap: float | None = None  # the A/D line below its 52-week high, share of its range
    # C. leadership
    tt_share: float | None = None
    tt_count: int | None = None
    tt_count_prior: int | None = None  # caps.tt_collapse.sessions earlier
    leaders: int | None = None
    leader_change: float | None = None
    highs_20: float | None = None
    highs_52: float | None = None
    sectors: float | None = None
    leaders_vs_vqi: float | None = None
    # D. accumulation / distribution
    dist_nifty: int | None = None
    dist_vqi: int | None = None
    acc_nifty: int | None = None
    up_down_value: float | None = None
    dist_cluster: int | None = None
    up_on_volume: float | None = None
    # E. breakout environment
    judged: int = 0
    success: float | None = None
    held: float | None = None
    failure: float | None = None
    median_return: float | None = None
    near_pivot: float | None = None
    # events
    thrust: bool = False


@dataclass(frozen=True)
class Part:
    id: str
    reading: float | None
    points: float | None
    weight: float


@dataclass(frozen=True)
class Pillar:
    id: str
    weight: float
    score: float | None  # None: missing
    parts: list[Part]
    note: str = ""


@dataclass(frozen=True)
class CapHit:
    id: str
    max: Label
    reason: str


@dataclass
class DayScore:
    raw: float | None
    raw_label: Label | None
    label: Label | None
    complete: bool
    exposure: str | None
    pillars: list[Pillar]
    caps: list[CapHit] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    thrust: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rung(id_: str, reading: float | None, rung: Rung) -> Part:
    pts = None if reading is None else ladder(reading, rung.ladder)
    return Part(id_, reading, pts, rung.weight)


def _combine(id_: str, weight: float, parts: list[Part], note: str = "") -> Pillar:
    """Weighted mean of the parts that could be measured; missing when none could."""
    known = [(p.points, p.weight) for p in parts if p.points is not None and p.weight > 0]
    total = sum(w for _, w in known)
    score = sum(pts * w for pts, w in known) / total if total else None
    return Pillar(id_, weight, score, parts, note)


def index_trend(f: DayFeatures, s: HealthSettings) -> Pillar:
    c = s.index_trend
    w = s.pillars["index_trend"]
    close, m50, m150, m200 = f.n500_close, f.n500_sma50, f.n500_sma150, f.n500_sma200
    if close is None or m50 is None or m150 is None or m200 is None:
        return Pillar("index_trend", w, None, [], "NIFTY 500 or its averages are not available.")

    def yes(ok: bool) -> float:
        return 100.0 if ok else 0.0

    def pct(a: float, b: float) -> float:
        return (a / b - 1.0) * 100.0

    stacked = int(m50 > m150) + int(m150 > m200)
    parts = [
        Part("above_50", pct(close, m50), yes(close > m50), c.above_50),
        Part("above_150", pct(close, m150), yes(close > m150), c.above_150),
        Part("above_200", pct(close, m200), yes(close > m200), c.above_200),
        Part("stacked", float(stacked), 50.0 * stacked, c.stacked),
    ]
    prior = f.n500_sma200_prior
    parts.append(Part(
        "rising_200", None if prior is None else pct(m200, prior),
        None if prior is None else yes(m200 > prior), c.rising_200,
    ))  # fmt: skip
    for id_, flag, pts in (
        ("nifty50", f.nifty50_above_50, c.confirm_nifty50),
        ("smallcap250", f.smallcap_above_50, c.confirm_smallcap),
    ):
        parts.append(Part(id_, None if flag is None else float(flag),
                          None if flag is None else yes(flag), pts))  # fmt: skip
    return _combine("index_trend", w, parts)


def breadth(f: DayFeatures, s: HealthSettings) -> Pillar:
    b = s.breadth
    w = s.pillars["breadth"]
    if f.universe < s.min_universe:
        return Pillar("breadth", w, None, [], f"Only {f.universe} eligible stocks.")
    hl = f.highs_lows
    return _combine("breadth", w, [
        _rung("above_50", f.pct_above_50, b.above_50),
        _rung("above_150", f.pct_above_150, b.above_150),
        _rung("above_200", f.pct_above_200, b.above_200),
        _rung("ad_trend", f.ad_ratio, b.ad_trend),
        # no new highs and no new lows at all reads as neutral, not as missing
        Part("highs_lows", hl, 50.0 if hl is None else ladder(hl, b.highs_lows.ladder),
             b.highs_lows.weight),
        _rung("change", f.above_50_change, b.change),
        _rung("vqi_gap", f.vqi_gap, b.vqi_gap),
    ])  # fmt: skip


def leadership(f: DayFeatures, s: HealthSettings) -> Pillar:
    lc = s.leadership
    w = s.pillars["leadership"]
    if f.universe < s.min_universe:
        return Pillar("leadership", w, None, [], f"Only {f.universe} eligible stocks.")
    return _combine("leadership", w, [
        _rung("tt_share", f.tt_share, lc.tt_share),
        _rung("leader_change", f.leader_change, lc.leader_change),
        _rung("highs_20", f.highs_20, lc.highs_20),
        _rung("highs_52", f.highs_52, lc.highs_52),
        _rung("sectors", f.sectors, lc.sectors),
        _rung("leaders_vs_vqi", f.leaders_vs_vqi, lc.leaders_vs_vqi),
    ])  # fmt: skip


def accumulation(f: DayFeatures, s: HealthSettings) -> Pillar:
    a = s.accumulation
    w = s.pillars["accumulation"]
    dist: float | None
    if f.dist_nifty is not None and f.dist_vqi is not None:
        dist = a.nifty_share * f.dist_nifty + (1 - a.nifty_share) * f.dist_vqi
    else:
        dist = f.dist_nifty if f.dist_nifty is not None else f.dist_vqi
    uov = f.up_on_volume
    return _combine("accumulation", w, [
        _rung("distribution", dist, a.distribution),
        _rung("accumulation_days", None if f.acc_nifty is None else float(f.acc_nifty),
              a.accumulation_days),
        _rung("up_down_value", f.up_down_value, a.up_down_value),
        _rung("clustering", None if f.dist_cluster is None else float(f.dist_cluster),
              a.clustering),
        Part("up_on_volume", uov, None if f.acc_nifty is None else
             50.0 if uov is None else ladder(uov, a.up_on_volume.ladder), a.up_on_volume.weight),
    ])  # fmt: skip


def breakouts(f: DayFeatures, s: HealthSettings) -> Pillar:
    e = s.breakouts
    w = s.pillars["breakouts"]
    if f.judged < e.min_judged:
        return Pillar("breakouts", w, None, [],
                      f"{f.judged} breakouts judged in {e.window} sessions; "
                      f"{e.min_judged} are needed.")  # fmt: skip
    return _combine("breakouts", w, [
        _rung("success", f.success, e.success),
        _rung("held", f.held, e.held),
        _rung("failure", f.failure, e.failure),
        _rung("median_return", f.median_return, e.median_return),
        _rung("near_pivot", f.near_pivot, e.near_pivot),
    ])  # fmt: skip


def label_of(score: float, s: HealthSettings) -> Label:
    for floor, name in s.labels:
        if score >= floor:
            return name
    return s.labels[-1][1]


def worse(a: Label, b: Label) -> Label:
    return a if LABEL_ORDER.index(a) >= LABEL_ORDER.index(b) else b


def caps(f: DayFeatures, s: HealthSettings, complete: bool) -> list[CapHit]:
    """Every cap that holds on the day; the label can be no better than the worst of them."""
    c = s.caps
    out: list[CapHit] = []
    m200, prior = f.n500_sma200, f.n500_sma200_prior
    if (
        f.n500_close is not None and m200 is not None and prior is not None
        and f.n500_close < m200 and m200 < prior
        and f.pct_above_50 is not None and f.pct_above_50 < (c.downtrend.above_50_below or 0)
    ):  # fmt: skip
        out.append(CapHit(
            "downtrend", c.downtrend.max,
            f"NIFTY 500 is below a falling 200-day average and only {f.pct_above_50:.0f}% of "
            "our stocks are above their 50-day.",
        ))  # fmt: skip
    if f.dist_cluster is not None and f.dist_cluster >= (c.distribution.days or 0):
        out.append(CapHit("distribution", c.distribution.max,
                          f"{f.dist_cluster} NIFTY 500 distribution days in the last "
                          f"{s.accumulation.cluster_sessions} sessions."))  # fmt: skip
    if (
        f.held is not None and f.judged >= (c.failures.min_judged or 0)
        and 100.0 - f.held > (c.failures.rate_above or 100)
    ):  # fmt: skip
        out.append(CapHit("failures", c.failures.max,
                          f"{100 - f.held:.0f}% of {f.judged} breakouts closed back below "
                          f"their pivot within {s.breakouts.hold_sessions} sessions."))  # fmt: skip
    if f.tt_count is not None and f.tt_count_prior:
        drop = 1.0 - f.tt_count / f.tt_count_prior
        if drop >= (c.tt_collapse.drop or 1.0):
            out.append(CapHit("tt_collapse", c.tt_collapse.max,
                              f"Trend Template passes fell from {f.tt_count_prior} to "
                              f"{f.tt_count} in {c.tt_collapse.sessions} sessions."))  # fmt: skip
    if (
        f.n500_close is not None and f.n500_high_252
        and f.n500_close >= f.n500_high_252 * (1 - (c.divergence.near_high or 0))
        and f.ad_line_gap is not None and f.ad_line_gap > (c.divergence.gap or 1.0)
    ):  # fmt: skip
        out.append(CapHit("divergence", c.divergence.max,
                          "NIFTY 500 is near its 52-week high but the advance/decline line "
                          "is well below its own."))  # fmt: skip
    if not complete:
        out.append(CapHit("incomplete", c.incomplete.max, "A pillar could not be measured."))
    return out


def score_day(f: DayFeatures, s: HealthSettings) -> DayScore:
    pillars = [index_trend(f, s), breadth(f, s), leadership(f, s), accumulation(f, s),
               breakouts(f, s)]  # fmt: skip
    assert tuple(p.id for p in pillars) == PILLARS
    known = [(p.score, p.weight) for p in pillars if p.score is not None and p.weight > 0]
    missing = [p.id for p in pillars if p.score is None]
    total = sum(w for _, w in known)
    if not total:
        return DayScore(None, None, None, False, None, pillars, [], missing, f.thrust)
    raw = sum(sc * w for sc, w in known) / total
    raw_label = label_of(raw, s)
    hits = caps(f, s, complete=not missing)
    label = raw_label
    for h in hits:
        label = worse(label, h.max)
    return DayScore(round(raw, 1), raw_label, label, not missing, s.exposure[label], pillars,
                    hits, missing, f.thrust)  # fmt: skip
