"""Outcome study: does the detector's class pick setups that work? (VCP_SPECIFICATION 53, 62, 63)

Replaces human labels as the tuning evidence (owner decision 2026-10-03, option B): the market
labels each scan-date window after the fact, and the detector's classes are compared on what
happened next. Labels use only bars *after* the as-of date (section 54); the detector sees only
bars up to it.

Per window (a Trend Template passer with weekly Stage 2 on a scan date):

* entry = the as-of close (the scan runs after the close; next-day fills would differ by one
  bar for every group alike, so comparisons between groups are fair);
* ``ret_20`` / ``ret_60``: close-to-close returns after 20 / 60 sessions;
* ``mfe_60`` / ``mae_60``: highest high / lowest low within 60 sessions vs entry;
* ``result``: ``WIN`` if a high reaches +``WIN_PCT`` before a low reaches -``LOSS_PCT`` within 60
  sessions, ``LOSS`` the other way (both on one bar counts as LOSS, conservatively), ``NONE``
  if neither;
* ``breakout_20``: a close above the primary pivot within 20 sessions (patterns with a pivot).

* **breakout trade** (how a VCP is actually traded, section 45): entry at the first close
  above the primary pivot within ``BREAKOUT_WINDOW`` sessions on volume >=
  ``BREAKOUT_VOLUME`` x the mean of the 50 bars before it; exit at +``WIN_PCT`` (target),
  -``LOSS_PCT`` (stop; first if both on one bar) or the close ``HORIZON`` sessions after entry.
  ``trade_ret`` is that exit return; no qualifying breakout = no trade. The scan-date entry
  above treats every passer as bought on the scan day, which is unfair to forming patterns
  that a VCP trader would only buy on a breakout (found 2026-10-03).

Windows without 60 forward sessions are left out (counted as ``censored``); a trade needs
``HORIZON`` sessions after its entry.

Groups: the detector class, with VCPs missing exactly one A+ rule split out as NEAR_A_PLUS, and
NO_PATTERN. Market regime matters (a strong month lifts every group), so each group is also
compared with the **same dates' passers**: ``excess_win`` = its win rate minus the average
win rate of all passers on the dates of its windows.

Periods: ``development`` (as-of up to ``split_date``) is where thresholds may be compared;
``validation`` (after it) is looked at once for a chosen configuration (section 62).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

WIN_PCT = 10.0
LOSS_PCT = 7.0
HORIZON = 60
BREAKOUT_WINDOW = 20
BREAKOUT_VOLUME = 1.5


@dataclass(frozen=True, slots=True)
class Outcome:
    ret_20: float
    ret_60: float
    mfe_60: float
    mae_60: float
    result: str  # WIN | LOSS | NONE
    breakout_20: bool | None
    trade_ret: float | None = None  # breakout trade exit return %; None = no trade


@dataclass(frozen=True, slots=True)
class WindowRow:
    as_of: date
    instrument_id: str
    symbol: str
    group: str
    status: str | None
    outcome: Outcome | None  # None = censored (fewer than HORIZON forward sessions)


def _first_hit(
    high: Sequence[float], low: Sequence[float], start: int, entry: float
) -> tuple[str, int]:
    """WIN / LOSS / NONE for bars ``start .. start + HORIZON - 1`` and the exit bar index."""
    up, down = entry * (1 + WIN_PCT / 100), entry * (1 - LOSS_PCT / 100)
    for i in range(start, start + HORIZON):
        if low[i] <= down:
            return "LOSS", i
        if high[i] >= up:
            return "WIN", i
    return "NONE", start + HORIZON - 1


def breakout_trade(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[float | None],
    prior_volume: Sequence[float | None],
    pivot: float | None,
) -> float | None:
    """Exit return % of the breakout trade (module docstring), or None for no trade."""
    if pivot is None:
        return None
    vols = list(prior_volume) + list(volume)
    offset = len(prior_volume)
    for i in range(min(BREAKOUT_WINDOW, len(close))):
        if close[i] <= pivot:
            continue
        window = vols[offset + i - 50 : offset + i]
        v = volume[i]
        if len(window) < 50 or v is None or any(x is None for x in window):
            continue
        base = sum(x for x in window if x is not None) / 50
        if base <= 0 or v < BREAKOUT_VOLUME * base:
            continue
        if i + HORIZON >= len(close):
            return None  # not enough bars after entry: treated as censored, no trade
        result, j = _first_hit(high, low, i + 1, close[i])
        if result == "WIN":
            return WIN_PCT
        if result == "LOSS":
            return -LOSS_PCT
        return (close[j] / close[i] - 1) * 100
    return None


def forward_outcome(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    entry: float,
    pivot: float | None,
    volume: Sequence[float | None] | None = None,
    prior_volume: Sequence[float | None] | None = None,
) -> Outcome | None:
    """Outcome from the bars *after* the as-of date (module docstring); None if too few."""
    if len(close) < HORIZON:
        return None
    result, _ = _first_hit(high, low, 0, entry)
    breakout = None if pivot is None else any(c > pivot for c in close[:BREAKOUT_WINDOW])
    return Outcome(
        ret_20=(close[BREAKOUT_WINDOW - 1] / entry - 1) * 100,
        ret_60=(close[HORIZON - 1] / entry - 1) * 100,
        mfe_60=(max(high[:HORIZON]) / entry - 1) * 100,
        mae_60=(min(low[:HORIZON]) / entry - 1) * 100,
        result=result,
        breakout_20=breakout,
        trade_ret=(
            breakout_trade(high, low, close, volume, prior_volume, pivot)
            if volume is not None and prior_volume is not None
            else None
        ),
    )


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson interval for k successes in n (as fractions); (0, 1) when n = 0."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def group_of(det: object) -> tuple[str, str | None, float | None]:
    """(group, status, primary pivot price) of a detection (module docstring)."""
    from vcp_scanner.domain.enums import VCPClassification
    from vcp_scanner.patterns.vcp.detector import VCPDetection

    assert isinstance(det, VCPDetection)
    p = det.pattern
    if p is None:
        return "NO_PATTERN", None, None
    group = p.classification.value
    if (
        p.classification is VCPClassification.VCP
        and det.classification is not None
        and len(det.classification.unmet[VCPClassification.A_PLUS_VCP]) == 1
    ):
        group = "NEAR_A_PLUS"
    return group, p.status.value, p.pivot.pivot_price if p.pivot else None


def collect_windows(
    store: object,
    dates: Sequence[date],
    config_hash: str,
    variants: dict[str, tuple[object, object]],
) -> dict[str, list[WindowRow]]:
    """Every passer window on ``dates`` under each named (vcp, classification) config."""
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
    from vcp_scanner.patterns.vcp.detector import VCPDetector
    from vcp_scanner.patterns.vcp.measurements import PriceSeries

    assert isinstance(store, DuckDBStore)
    passers: list[tuple[date, str, str, bool | None]] = []
    for d in sorted(dates):
        passers += [(d, *r) for r in store.conn.execute(
            "SELECT t.instrument_id, i.symbol, t.weekly_stage2_pass FROM trend_template_results t"
            " JOIN instruments i USING (instrument_id) WHERE t.scan_id = ? AND t.status = 'PASS'"
            " AND t.weekly_stage2_pass ORDER BY t.instrument_id",
            [f"trend-{d.isoformat()}-{config_hash[:12]}"]).fetchall()]  # fmt: skip
    ids = sorted({p[1] for p in passers})
    bars: dict[str, list[Any]] = defaultdict(list)
    for r in store.conn.execute(
        """
        SELECT p.instrument_id, p.trade_date, p.high_adj, p.low_adj, p.close_adj, p.volume_adj,
               f.atr_pct_14
        FROM daily_prices_adjusted_current p LEFT JOIN technical_features_daily f
          ON f.instrument_id = p.instrument_id AND f.trade_date = p.trade_date
         AND f.calculation_version = ? AND f.data_snapshot_id = ?
        WHERE p.computed_from_snapshot_id = ? AND p.trade_date >= ?
          AND p.instrument_id IN (SELECT unnest(?))
        ORDER BY p.instrument_id, p.trade_date
        """,
        [FEATURES_CALCULATION_VERSION, "LIVE", "LIVE", date(min(dates).year - 2, 1, 1), ids],
    ).fetchall():
        bars[str(r[0])].append(r[1:])

    detectors = {
        name: (VCPDetector(v, c, config_hash=name), v)  # type: ignore[arg-type]
        for name, (v, c) in variants.items()
    }
    out: dict[str, list[WindowRow]] = {name: [] for name in variants}
    for d, iid, symbol, stage2 in passers:
        b: list[Any] = bars.get(iid, [])
        k = next((j for j, x in enumerate(b) if x[0] == d), None)
        if k is None:
            continue  # no bar on the scan date
        fwd = b[k + 1 :]
        entry = float(b[k][3])
        f_high = [float(x[1]) for x in fwd]
        f_low = [float(x[2]) for x in fwd]
        f_close = [float(x[3]) for x in fwd]
        f_vol = [None if x[4] is None else float(x[4]) for x in fwd]
        p_vol = [None if x[4] is None else float(x[4]) for x in b[max(0, k + 1 - 50) : k + 1]]
        for name, (det, v) in detectors.items():
            past = b[max(0, k + 1 - v.lookback_bars()) : k + 1]  # type: ignore[attr-defined]
            s = PriceSeries(
                [x[0] for x in past], [float(x[1]) for x in past], [float(x[2]) for x in past],
                [float(x[3]) for x in past], [None if x[4] is None else float(x[4]) for x in past],
                [None if x[5] is None else float(x[5]) for x in past],
            )  # fmt: skip
            found = det.detect(iid, s, d, trend_template_pass=True, weekly_stage2_pass=stage2)
            group, status, pivot = group_of(found)
            outcome = forward_outcome(f_high, f_low, f_close, entry, pivot, f_vol, p_vol)
            out[name].append(WindowRow(d, iid, str(symbol), group, status, outcome))
    return out


@dataclass(frozen=True, slots=True)
class GroupStats:
    group: str
    n: int
    wins: int
    losses: int
    win_rate: float | None
    ci: tuple[float, float]
    excess_win: float | None
    median_ret_20: float | None
    median_ret_60: float | None
    median_mae_60: float | None
    breakout_rate: float | None
    trades: int = 0
    trade_win_rate: float | None = None
    expectancy: float | None = None


GROUP_ORDER = ("A_PLUS_VCP", "NEAR_A_PLUS", "VCP", "VCP_LIKE", "NONE", "NO_PATTERN", "ALL")


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def summarize(rows: Sequence[WindowRow], key: str = "group") -> tuple[list[GroupStats], int]:
    """Stats per group (or per status with ``key="status"``) plus ALL; returns (stats,
    censored count). ``excess_win`` compares with the same dates' passers."""
    done = [r for r in rows if r.outcome is not None]
    censored = len(rows) - len(done)
    by_date: dict[date, list[WindowRow]] = defaultdict(list)
    for r in done:
        by_date[r.as_of].append(r)
    date_rate = {d: sum(r.outcome.result == "WIN" for r in rs) / len(rs)  # type: ignore[union-attr]
                 for d, rs in by_date.items()}  # fmt: skip
    groups: dict[str, list[WindowRow]] = defaultdict(list)
    for r in done:
        groups[str(getattr(r, key))].append(r)
        groups["ALL"].append(r)
    stats = []
    for g, rs in groups.items():
        outs = [r.outcome for r in rs if r.outcome is not None]
        wins = sum(o.result == "WIN" for o in outs)
        losses = sum(o.result == "LOSS" for o in outs)
        n = len(outs)
        expected = sum(date_rate[r.as_of] for r in rs) / n if n else None
        bo = [o.breakout_20 for o in outs if o.breakout_20 is not None]
        tr = [o.trade_ret for o in outs if o.trade_ret is not None]
        stats.append(GroupStats(
            g, n, wins, losses, wins / n if n else None, wilson(wins, n),
            (wins / n - expected) if n and expected is not None else None,
            _median([o.ret_20 for o in outs]), _median([o.ret_60 for o in outs]),
            _median([o.mae_60 for o in outs]), (sum(bo) / len(bo)) if bo else None,
            len(tr), (sum(x > 0 for x in tr) / len(tr)) if tr else None,
            (sum(tr) / len(tr)) if tr else None,
        ))  # fmt: skip
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    stats.sort(key=lambda s: (order.get(s.group, 50), s.group))
    return stats, censored


def format_stats(stats: Sequence[GroupStats], title: str) -> str:
    def pct(x: float | None) -> str:
        return "    -" if x is None else f"{x * 100:5.1f}"

    def num(x: float | None) -> str:
        return "    -" if x is None else f"{x:5.1f}"

    lines = [title, f"  {'group':12} {'n':>5} {'win%':>5} {'95% CI':>13} {'excess':>6} "
             f"{'ret20':>5} {'ret60':>5} {'MAE60':>5} {'bo20%':>5} | {'trades':>6} "
             f"{'twin%':>5} {'exp%':>5}"]  # fmt: skip
    for s in stats:
        lines.append(
            f"  {s.group:12} {s.n:5d} {pct(s.win_rate)} {pct(s.ci[0])}-{pct(s.ci[1]).strip():>5}"
            f" {pct(s.excess_win):>6} {num(s.median_ret_20)} {num(s.median_ret_60)}"
            f" {num(s.median_mae_60)} {pct(s.breakout_rate)} | {s.trades:6d}"
            f" {pct(s.trade_win_rate)} {num(s.expectancy)}"
        )
    return "\n".join(lines)
