"""Daily features of the Market Health Score (MARKET_HEALTH_SPECIFICATION §3), for every session
from the stored prices: our universe rebuilt day by day from prices (series, price, traded value,
history; ASM/GSM history is not stored, so the rebuilt universe keeps a few stocks that were on
those lists), relative strength and the Trend Template recomputed per day the way the scan does,
and NIFTY 500, NIFTY 50, Smallcap 250 and the sector indices from ``index_daily``.

One rule over all history, so a day of 2022 and today are scored the same way."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any

from vcp_scanner.config.models import TrendTemplateConfig, UniverseConfig
from vcp_scanner.market_health.scoring import DayFeatures
from vcp_scanner.market_health.settings import HealthSettings

NIFTY500, NIFTY50, SMALLCAP = "Nifty 500", "Nifty 50", "Nifty Smallcap 250"
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
MIN_SECTORS = 8  # fewer sector indices with history on a day: the sector reading is missing
RS_WINDOWS = (63, 126, 189, 252)
RS_WEIGHTS = (0.40, 0.20, 0.20, 0.20)


def _stock_sql(s: HealthSettings, tt: TrendTemplateConfig, uni: UniverseConfig) -> str:
    """Per stock and session: eligibility, RS rank, Trend Template, breakout and forward closes.
    Integers and floats come from validated settings, so they are formatted in."""
    e = s.breakouts
    lag = tt.sma200_slope_lookback_days
    rs = " + ".join(f"{w} * (c / c{n} - 1)" for w, n in zip(RS_WEIGHTS, RS_WINDOWS, strict=True))
    rs_ok = " AND ".join(f"c{n} > 0" for n in RS_WINDOWS)
    lags = ", ".join(f"lag(c, {n}) OVER s AS c{n}" for n in RS_WINDOWS)
    return f"""
CREATE OR REPLACE TEMP TABLE mh_stock AS
WITH ser AS (
    SELECT instrument_id, trade_date, bool_or(series IN (SELECT unnest(?))) AS eq
    FROM daily_series GROUP BY 1, 2
), base AS (
    SELECT p.instrument_id AS iid, p.trade_date AS d, p.close_adj AS c, p.high_adj AS h,
           p.low_adj AS l, p.volume_adj AS v, r.close_raw AS craw, r.close_raw * r.volume_raw AS tv,
           coalesce(ser.eq, false) AS eq,
           f.sma_50, f.sma_150, f.sma_200, f.high_20, f.high_50, f.high_252, f.low_252,
           f.daily_return AS ret, f.volume_avg_50, f.rolling_volatility_20 AS vol20,
           f.rolling_volatility_50 AS vol50
    FROM daily_prices_adjusted_current p
    JOIN technical_features_daily f
      ON f.instrument_id = p.instrument_id AND f.trade_date = p.trade_date
     AND f.calculation_version = ? AND f.data_snapshot_id = ?
    JOIN daily_prices r
      ON r.instrument_id = p.instrument_id AND r.trade_date = p.trade_date AND r.known_to IS NULL
    LEFT JOIN ser ON ser.instrument_id = p.instrument_id AND ser.trade_date = p.trade_date
    WHERE p.computed_from_snapshot_id = ?
), w AS (
    SELECT *,
        row_number() OVER s AS nhist,
        avg(tv) OVER (s ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS atv20,
        avg(tv) OVER (s ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS atv50,
        lag(sma_200, {lag}) OVER s AS sma200_prior,
        {lags},
        lag(high_50) OVER s AS pvt,
        lag(high_50, {e.base_sessions + 1}) OVER s AS pvt_base,
        lag(volume_avg_50) OVER s AS vavg_prev,
        lead(c, 5) OVER s AS c_f5,
        lead(c, {e.judge_sessions}) OVER s AS c_fj,
        min(c) OVER (s ROWS BETWEEN 1 FOLLOWING AND {e.hold_sessions} FOLLOWING) AS min_fh,
        min(c) OVER (s ROWS BETWEEN 1 FOLLOWING AND {e.judge_sessions} FOLLOWING) AS min_fj,
        count(*) OVER (s ROWS BETWEEN 1 FOLLOWING AND {e.judge_sessions} FOLLOWING) AS n_fj
    FROM base WINDOW s AS (PARTITION BY iid ORDER BY d)
), elig AS (
    SELECT *,
        eq AND craw >= {uni.min_close_price} AND atv20 >= {uni.min_daily_turnover_inr}
        AND atv50 >= {uni.min_avg_traded_value_50d_inr} AND nhist >= {uni.min_history_days} AS ok
    FROM w
), raw_rs AS (
    SELECT *, CASE WHEN ok AND {rs_ok} THEN {rs} END AS rs_raw FROM elig
), ranked AS (
    SELECT *, CASE WHEN rs_raw IS NOT NULL THEN 1 + floor(98 * percent_rank() OVER (
        PARTITION BY d, rs_raw IS NULL ORDER BY rs_raw)) END AS rs_rank
    FROM raw_rs
), judged AS (
    SELECT *,
        ok AND sma_50 IS NOT NULL AND sma_150 IS NOT NULL AND sma_200 IS NOT NULL
        AND sma200_prior IS NOT NULL AND high_252 IS NOT NULL AND low_252 IS NOT NULL
        AND rs_rank IS NOT NULL AS tt_eval
    FROM ranked
)
SELECT *,
    tt_eval AND c > sma_150 AND c > sma_200 AND sma_150 > sma_200 AND sma_200 > sma200_prior
    AND sma_50 > sma_150 AND sma_50 > sma_200 AND c > sma_50
    AND c >= low_252 * {1 + tt.min_above_52w_low_pct / 100}
    AND c >= high_252 * {1 - tt.max_below_52w_high_pct / 100}
    AND rs_rank >= {tt.min_rs_rank} AS tt
FROM judged
"""


def _daily_sql(s: HealthSettings) -> str:
    ls = s.leadership.leader_sessions
    rs = s.leadership.leader_rs
    gap = s.breakouts.near_pivot_gap
    return f"""
WITH x AS (
    SELECT *, coalesce(tt, false) AND rs_rank >= {rs} AS leader,
        lag(coalesce(tt, false) AND rs_rank >= {rs}, {ls})
            OVER (PARTITION BY iid ORDER BY d) AS leader_then,
        lag(c, {ls}) OVER (PARTITION BY iid ORDER BY d) AS c_then
    FROM mh_stock
)
SELECT d,
    count(*) FILTER (WHERE ok) AS n,
    count(*) FILTER (WHERE ok AND sma_50 IS NOT NULL) AS n50,
    count(*) FILTER (WHERE ok AND c > sma_50) AS a50,
    count(*) FILTER (WHERE ok AND sma_150 IS NOT NULL) AS n150,
    count(*) FILTER (WHERE ok AND c > sma_150) AS a150,
    count(*) FILTER (WHERE ok AND sma_200 IS NOT NULL) AS n200,
    count(*) FILTER (WHERE ok AND c > sma_200) AS a200,
    count(*) FILTER (WHERE ok AND ret > 0) AS adv,
    count(*) FILTER (WHERE ok AND ret < 0) AS dec,
    count(*) FILTER (WHERE ok AND h >= high_252 * 0.99999) AS hi52,
    count(*) FILTER (WHERE ok AND l <= low_252 * 1.00001) AS lo52,
    count(*) FILTER (WHERE ok AND h >= high_20 * 0.99999) AS hi20,
    count(*) FILTER (WHERE tt_eval) AS tt_n,
    count(*) FILTER (WHERE tt) AS tt_pass,
    count(*) FILTER (WHERE leader) AS leaders,
    sum(tv) FILTER (WHERE ok AND ret > 0) AS up_tv,
    sum(tv) FILTER (WHERE ok AND ret < 0) AS down_tv,
    avg(ret) FILTER (WHERE ok) AS mean_ret,
    sum(v) FILTER (WHERE ok) AS vol,
    count(*) FILTER (WHERE tt AND c >= high_50 * {1 - gap} AND vol20 < vol50) AS near,
    avg(c / c_then - 1) FILTER (WHERE leader_then AND c_then > 0) AS lead_ret,
    count(*) FILTER (WHERE leader_then AND c_then > 0) AS lead_n
FROM x GROUP BY d ORDER BY d
"""


def _breakout_sql(s: HealthSettings) -> str:
    e = s.breakouts
    return f"""
SELECT d, iid, pvt, c, c_f5, c_fj, min_fh, min_fj
FROM mh_stock
WHERE tt AND pvt IS NOT NULL AND pvt_base IS NOT NULL AND pvt <= pvt_base
  AND c > pvt AND vavg_prev > 0 AND v >= {e.volume_ratio} * vavg_prev
  AND n_fj = {e.judge_sessions}
ORDER BY d, iid
"""


@dataclass(frozen=True)
class Breakout:
    day: date
    held: bool
    success: bool
    failed: bool
    ret5: float | None
    retj: float | None


def load_breakouts(cur: Any, s: HealthSettings) -> list[Breakout]:
    e = s.breakouts
    out = []
    for d, _iid, pivot, c, c5, cj, min_h, min_j in cur.execute(_breakout_sql(s)).fetchall():
        out.append(Breakout(
            day=d, held=min_h >= pivot, success=cj >= pivot * (1 + e.follow_through),
            failed=min_j < pivot * (1 - e.hard_fail),
            ret5=None if c5 is None else (c5 / c - 1) * 100, retj=(cj / c - 1) * 100,
        ))  # fmt: skip
    return out


class Series:
    """One index's closes (and volumes) in date order, with lookups by session."""

    def __init__(self, rows: list[tuple[date, float, float | None]]) -> None:
        self.days = [r[0] for r in rows]
        self.close = [float(r[1]) for r in rows]
        self.volume = [None if r[2] is None else float(r[2]) for r in rows]
        self.pos = {d: i for i, d in enumerate(self.days)}

    def sma(self, i: int, n: int) -> float | None:
        return sum(self.close[i - n + 1 : i + 1]) / n if i >= n - 1 else None

    def ret(self, i: int, n: int) -> float | None:
        return self.close[i] / self.close[i - n] - 1 if i >= n and self.close[i - n] else None

    def kinds(self, move: float) -> list[str | None]:
        """'distribution' (down `move` or more on higher volume than the session before),
        'accumulation' (up as much on higher volume) or None, per session."""
        out: list[str | None] = [None]
        for i in range(1, len(self.close)):
            v, pv = self.volume[i], self.volume[i - 1]
            r = self.close[i] / self.close[i - 1] - 1 if self.close[i - 1] else 0.0
            heavier = v is not None and pv is not None and v > pv
            out.append("distribution" if heavier and r <= -move
                       else "accumulation" if heavier and r >= move else None)  # fmt: skip
        return out[: len(self.close)]


def load_indices(cur: Any, names: list[str]) -> dict[str, Series]:
    rows: dict[str, list[tuple[date, float, float | None]]] = {n: [] for n in names}
    for name, d, close, vol in cur.execute(
        "SELECT index_name, trade_date, close, volume FROM index_daily"
        " WHERE index_name IN (SELECT unnest(?)) AND close > 0 ORDER BY 1, 2",
        [names],
    ).fetchall():
        rows[str(name)].append((d, float(close), None if vol is None else float(vol)))
    return {n: Series(r) for n, r in rows.items() if r}


def _count(kinds: list[str | None], i: int, n: int, kind: str) -> int:
    return sum(k == kind for k in kinds[max(1, i - n + 1) : i + 1])


def _avg(values: list[float | None], i: int, n: int) -> float | None:
    window = values[max(0, i - n + 1) : i + 1]
    if i < n - 1 or any(v is None for v in window):
        return None
    return sum(window) / n  # type: ignore[arg-type]


def _ratio(a: float, b: float) -> float | None:
    return a / b if b else None


@dataclass(frozen=True)
class FeatureDay:
    day: date
    features: DayFeatures
    vqi: float | None
    nifty500: float | None


def _above(series: Series | None, day: date, n: int = 50) -> bool | None:
    if series is None or (j := series.pos.get(day)) is None:
        return None
    ma = series.sma(j, n)
    return None if ma is None else series.close[j] > ma


def _sectors(idx: dict[str, Series], day: date, horizons: list[int]) -> float | None:
    """Share of sector indices in an uptrend (above their 50-day average with a positive return
    over the horizon), averaged over the horizons; None when too few sectors have history."""
    shares = []
    for h in horizons:
        ups = total = 0
        for name in SECTOR_INDEX.values():
            sr = idx.get(name)
            if sr is None or (j := sr.pos.get(day)) is None:
                continue
            ma, r = sr.sma(j, 50), sr.ret(j, h)
            if ma is None or r is None:
                continue
            total += 1
            ups += int(sr.close[j] > ma and r > 0)
        if total < MIN_SECTORS:
            return None
        shares.append(100.0 * ups / total)
    return sum(shares) / len(shares) if shares else None


def _thrust(adv: list[int], dec: list[int], s: HealthSettings) -> list[bool]:
    """Zweig's breadth thrust on our universe: the day the `sessions`-day average of
    advancers / (advancers + decliners) first closes above `high` with a reading below `low` in
    the `sessions` sessions before it."""
    t = s.thrust
    share = [100.0 * a / (a + d) if a + d else None for a, d in zip(adv, dec, strict=True)]
    ratio = [_avg(share, i, t.sessions) for i in range(len(share))]
    out = [False] * len(ratio)
    for i in range(1, len(ratio)):
        now, before = ratio[i], ratio[i - 1]
        if now is None or now <= t.high or (before is not None and before > t.high):
            continue
        out[i] = any(v is not None and v < t.low for v in ratio[max(0, i - t.sessions) : i])
    return out


def compute_features(
    cur: Any, s: HealthSettings, tt: TrendTemplateConfig, uni: UniverseConfig,
    calculation_version: str, snapshot: str = "LIVE",
) -> list[FeatureDay]:  # fmt: skip
    """Features of every stored session, oldest first. Each day uses only data known that day
    (a breakout counts once its result is known, ``judge_sessions`` later)."""
    cur.execute(_stock_sql(s, tt, uni),
                [list(uni.eligible_series), calculation_version, snapshot, snapshot])  # fmt: skip
    res = cur.execute(_daily_sql(s))
    cols = [c[0] for c in res.description]
    rows = [dict(zip(cols, r, strict=True)) for r in res.fetchall()]
    outs = load_breakouts(cur, s)
    cur.execute("DROP TABLE IF EXISTS mh_stock")
    idx = load_indices(cur, [NIFTY500, NIFTY50, SMALLCAP, *SECTOR_INDEX.values()])
    return assemble(rows, outs, idx, s)


def assemble(
    rows: list[dict[str, Any]], outs: list[Breakout], idx: dict[str, Series], s: HealthSettings
) -> list[FeatureDay]:
    """The per-day readings from the per-day universe counts, the breakouts and the indices."""
    b, lc, a, e = s.breadth, s.leadership, s.accumulation, s.breakouts
    days = [r["d"] for r in rows]
    pos = {d: i for i, d in enumerate(days)}
    big = [int(r["n"]) >= s.min_universe for r in rows]
    n = [int(r["n"]) for r in rows]

    def pct(k: str, of: str, i: int) -> float | None:
        r = rows[i]
        return 100.0 * r[k] / r[of] if big[i] and r[of] else None

    vqi: list[float] = []
    level = 100.0
    for i, r in enumerate(rows):
        if big[i] and r["mean_ret"] is not None:
            level *= 1 + float(r["mean_ret"])
        vqi.append(level)
    ad_line: list[float] = []
    total = 0.0
    for r in rows:
        total += int(r["adv"]) - int(r["dec"])
        ad_line.append(total)
    vqi_kind: list[str | None] = [None]
    for i in range(1, len(rows)):
        r, p = rows[i], rows[i - 1]
        heavier = big[i] and big[i - 1] and (r["vol"] or 0) > (p["vol"] or 0)
        mr = float(r["mean_ret"] or 0.0)
        vqi_kind.append("distribution" if heavier and mr <= -a.day_move
                        else "accumulation" if heavier and mr >= a.day_move else None)  # fmt: skip
    judged_at: dict[int, list[Breakout]] = {}
    for o in outs:
        if (k := pos.get(o.day)) is not None:
            judged_at.setdefault(k + e.judge_sessions, []).append(o)
    n500 = idx.get(NIFTY500)
    n500_kind = n500.kinds(a.day_move) if n500 else []
    thrust = _thrust([int(r["adv"]) for r in rows], [int(r["dec"]) for r in rows], s)
    p50 = [pct("a50", "n50", i) for i in range(len(rows))]
    hi20 = [100.0 * r["hi20"] / r["n"] if big[i] else None for i, r in enumerate(rows)]
    hi52 = [100.0 * r["hi52"] / r["n"] if big[i] else None for i, r in enumerate(rows)]
    lo52 = [100.0 * r["lo52"] / r["n"] if big[i] else None for i, r in enumerate(rows)]

    out: list[FeatureDay] = []
    for i, d in enumerate(days):
        r = rows[i]
        f: dict[str, Any] = {"universe": n[i], "thrust": thrust[i]}
        # A. NIFTY 500 trend and the confirmations
        j = n500.pos.get(d) if n500 else None
        if n500 is not None and j is not None:
            f.update(n500_close=n500.close[j], n500_sma50=n500.sma(j, 50),
                     n500_sma150=n500.sma(j, 150), n500_sma200=n500.sma(j, 200),
                     n500_high_252=max(n500.close[max(0, j - 251) : j + 1]))  # fmt: skip
            sl = s.index_trend.slope_sessions
            f["n500_sma200_prior"] = n500.sma(j - sl, 200) if j >= sl else None
            if n500.volume[j] is not None and j >= a.window:
                dist = _count(n500_kind, j, a.window, "distribution")
                acc = _count(n500_kind, j, a.window, "accumulation")
                f.update(dist_nifty=dist, acc_nifty=acc,
                         dist_cluster=_count(n500_kind, j, a.cluster_sessions, "distribution"),
                         up_on_volume=_ratio(acc, acc + dist))  # fmt: skip
        f["nifty50_above_50"] = _above(idx.get(NIFTY50), d)
        f["smallcap_above_50"] = _above(idx.get(SMALLCAP), d)
        if big[i]:
            # B. breadth of our universe
            f.update(pct_above_50=p50[i], pct_above_150=pct("a150", "n150", i),
                     pct_above_200=pct("a200", "n200", i))  # fmt: skip
            w = rows[max(0, i - b.ad_sessions + 1) : i + 1]
            if i >= b.ad_sessions - 1 and all(big[i - k] for k in range(b.ad_sessions)):
                f["ad_ratio"] = _ratio(sum(int(x["adv"]) - int(x["dec"]) for x in w),
                                       sum(int(x["adv"]) + int(x["dec"]) for x in w))  # fmt: skip
            hi, lo = _avg(hi52, i, b.highs_lows_sessions), _avg(lo52, i, b.highs_lows_sessions)
            if hi is not None and lo is not None:
                f["highs_lows"] = _ratio(hi, hi + lo)
            k = i - b.change_sessions
            if k >= 0 and p50[k] is not None and p50[i] is not None:
                f["above_50_change"] = p50[i] - p50[k]  # type: ignore[operator]
            k = i - b.gap_sessions
            if n500 is not None and j is not None and k >= 0 and big[k]:
                jk = n500.pos.get(days[k])
                if jk is not None:
                    f["vqi_gap"] = 100.0 * ((vqi[i] / vqi[k] - 1)
                                            - (n500.close[j] / n500.close[jk] - 1))  # fmt: skip
            if i >= 251:
                hi_ad, lo_ad = max(ad_line[i - 251 : i + 1]), min(ad_line[i - 251 : i + 1])
                if hi_ad > lo_ad:
                    f["ad_line_gap"] = (hi_ad - ad_line[i]) / (hi_ad - lo_ad)
            # C. leadership
            f.update(tt_share=100.0 * r["tt_pass"] / n[i], tt_count=int(r["tt_pass"]),
                     leaders=int(r["leaders"]))  # fmt: skip
            k = i - s.caps.tt_collapse.sessions if s.caps.tt_collapse.sessions else -1
            if k >= 0 and big[k]:
                f["tt_count_prior"] = int(rows[k]["tt_pass"])
            k = i - lc.leader_sessions
            if k >= 0 and big[k]:
                then = int(rows[k]["leaders"])
                if then:
                    f["leader_change"] = 100.0 * (int(r["leaders"]) / then - 1)
                if int(r["lead_n"] or 0) >= 5 and r["lead_ret"] is not None:
                    f["leaders_vs_vqi"] = 100.0 * (float(r["lead_ret"]) - (vqi[i] / vqi[k] - 1))
            f["highs_20"] = _avg(hi20, i, lc.highs_sessions)
            f["highs_52"] = _avg(hi52, i, lc.highs_sessions)
            f["sectors"] = _sectors(idx, d, lc.sector_horizons)
            # D. our universe's side of accumulation / distribution
            if i >= a.window and all(big[i - k] for k in range(a.window + 1)):
                f["dist_vqi"] = _count(vqi_kind, i, a.window, "distribution")
            w = rows[max(0, i - a.value_sessions + 1) : i + 1]
            f["up_down_value"] = _ratio(sum(float(x["up_tv"] or 0) for x in w),
                                        sum(float(x["down_tv"] or 0) for x in w))  # fmt: skip
            # E. breakouts judged in the window
            judged = [o for k in range(i - e.window + 1, i + 1) for o in judged_at.get(k, [])]
            f["judged"] = len(judged)
            if judged:
                f.update(success=100.0 * sum(o.success for o in judged) / len(judged),
                         held=100.0 * sum(o.held for o in judged) / len(judged),
                         failure=100.0 * sum(o.failed for o in judged) / len(judged))  # fmt: skip
                r5 = [o.ret5 for o in judged if o.ret5 is not None]
                rj = [o.retj for o in judged if o.retj is not None]
                if r5 and rj:
                    f["median_return"] = (statistics.median(r5) + statistics.median(rj)) / 2
            f["near_pivot"] = 100.0 * r["near"] / n[i]
        out.append(FeatureDay(d, DayFeatures(**f), vqi[i] if big[i] else None,
                              None if n500 is None or j is None else n500.close[j]))  # fmt: skip
    return out
