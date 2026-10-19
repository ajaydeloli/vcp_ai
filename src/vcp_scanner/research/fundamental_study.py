"""F7: do fundamentals (and shareholding) separate good setups from bad ones?
FUNDAMENTALS_SPECIFICATION §12. A report only: nothing is fitted, nothing changes a score.

Rows are the labelled setups of the research database (``forward_labels``: the eligible setups
of each strategy with their forward returns), on the **development period only** unless the
validation period is asked for explicitly (§12: looked at once, and only if the development
result is worth it). Each setup gets, as of its scan day's close:

* the shadow score (SCORING_SPECIFICATION §7, ``fundamentals.research``) and its seven parts,
  and the hard-gate flag;
* exploratory: the latest shareholding pattern's one-quarter changes (promoter, FII, DII) and
  whether the pledge rose (§15).

Two views per key, as the earlier score study (``score_study``): quintiles within each scan
date (a strong or weak month lifts every bucket alike) and the information coefficient (the
Spearman correlation within each date, averaged over dates, with its t-statistic). Coverage
(the share of setups with the key) is always reported, and the outcomes of setups without a
score are shown beside those with one.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from vcp_scanner.research.score_study import spearman

DEVELOPMENT = (date(2022, 2, 1), date(2024, 6, 30))
VALIDATION = (date(2024, 7, 1), date(2026, 9, 30))
HORIZONS = (5, 10, 20, 40, 60)
BUCKETS = 5
MIN_PER_DATE = 10


@dataclass(slots=True)
class StudyRow:
    strategy: str
    instrument_id: str
    day: date
    returns: dict[int, float | None]  # horizon (sessions) -> return %, None: not yet known
    breakout_within_20: bool | None
    failed_breakout: bool | None
    keys: dict[str, float | None] = field(default_factory=dict)


Key = Callable[[StudyRow], float | None]


def key_of(name: str) -> Key:
    return lambda r: r.keys.get(name)


@dataclass(frozen=True, slots=True)
class Bucket:
    bucket: int  # 1 = lowest .. BUCKETS = highest
    n: int
    mean: dict[int, float | None]  # horizon -> mean return %
    median_60: float | None
    breakout_rate: float | None  # share with a breakout within 20 sessions
    failed_rate: float | None  # of those breakouts, the share that failed


@dataclass(frozen=True, slots=True)
class IC:
    mean: float | None
    t_stat: float | None
    dates: int


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _rate(flags: Sequence[bool | None]) -> float | None:
    known = [f for f in flags if f is not None]
    return sum(1 for f in known if f) / len(known) if known else None


def summarize(rows: Sequence[StudyRow]) -> Bucket:
    """Outcome summary of a group of setups (bucket 0)."""
    return _bucket(0, rows)


def _bucket(k: int, rows: Sequence[StudyRow]) -> Bucket:
    mean = {h: _mean([x for r in rows if (x := r.returns.get(h)) is not None]) for h in HORIZONS}
    r60 = [x for r in rows if (x := r.returns.get(60)) is not None]
    breakouts = [r for r in rows if r.breakout_within_20]
    return Bucket(k, len(rows), mean, _median(r60), _rate([r.breakout_within_20 for r in rows]),
                  _rate([r.failed_breakout for r in breakouts]))  # fmt: skip


def _by_date(rows: Sequence[StudyRow], key: Key) -> dict[date, list[tuple[float, StudyRow]]]:
    out: dict[date, list[tuple[float, StudyRow]]] = defaultdict(list)
    for r in rows:
        v = key(r)
        if v is not None and math.isfinite(v):
            out[r.day].append((v, r))
    return out


def quintiles(rows: Sequence[StudyRow], key: Key) -> list[Bucket]:
    """Buckets by rank within each scan date (dates with fewer than BUCKETS keyed setups are
    left out); ties keep their order of appearance."""
    groups: dict[int, list[StudyRow]] = defaultdict(list)
    for items in _by_date(rows, key).values():
        if len(items) < BUCKETS:
            continue
        items.sort(key=lambda t: t[0])
        n = len(items)
        for i, (_v, r) in enumerate(items):
            groups[min(BUCKETS, i * BUCKETS // n + 1)].append(r)
    return [_bucket(k, groups.get(k, [])) for k in range(1, BUCKETS + 1)]


def information_coefficient(rows: Sequence[StudyRow], key: Key, horizon: int) -> IC:
    ics: list[float] = []
    for items in _by_date(rows, key).values():
        pairs = [(v, x) for v, r in items if (x := r.returns.get(horizon)) is not None]
        if len(pairs) < MIN_PER_DATE:
            continue
        rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
        if rho is not None:
            ics.append(rho)
    if len(ics) < 2:
        return IC(_mean(ics), None, len(ics))
    m = sum(ics) / len(ics)
    sd = math.sqrt(sum((x - m) ** 2 for x in ics) / (len(ics) - 1))
    return IC(m, None if sd == 0 else m / sd * math.sqrt(len(ics)), len(ics))


@dataclass(frozen=True, slots=True)
class Spread:
    mean: float | None  # mean over dates of (group A mean return - group B mean return), pp
    t_stat: float | None
    dates: int


def _spread(diffs: list[float]) -> Spread:
    if len(diffs) < 2:
        return Spread(_mean(diffs), None, len(diffs))
    m = sum(diffs) / len(diffs)
    sd = math.sqrt(sum((x - m) ** 2 for x in diffs) / (len(diffs) - 1))
    return Spread(m, None if sd == 0 else m / sd * math.sqrt(len(diffs)), len(diffs))


def top_minus_bottom(rows: Sequence[StudyRow], key: Key, horizon: int) -> Spread:
    """Within each date (at least MIN_PER_DATE keyed setups): mean return of the top fifth by
    the key minus the bottom fifth; then averaged over dates."""
    diffs: list[float] = []
    for items in _by_date(rows, key).values():
        pairs = sorted((v, x) for v, r in items if (x := r.returns.get(horizon)) is not None)
        if len(pairs) < MIN_PER_DATE:
            continue
        k = len(pairs) // BUCKETS
        diffs.append(sum(x for _, x in pairs[-k:]) / k - sum(x for _, x in pairs[:k]) / k)
    return _spread(diffs)


def flag_within_date(
    rows: Sequence[StudyRow], name: str, horizon: int, min_each: int = 3
) -> Spread:
    """Within each date with at least ``min_each`` setups on each side: mean return where the
    0/1 key is 1 minus where it is 0; averaged over dates (the regime control for a flag)."""
    by: dict[date, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    for r in rows:
        g, x = r.keys.get(name), r.returns.get(horizon)
        if g is None or x is None:
            continue
        by[r.day][0 if g == 1.0 else 1].append(x)
    diffs = [sum(a) / len(a) - sum(b) / len(b) for a, b in by.values()
             if len(a) >= min_each and len(b) >= min_each]  # fmt: skip
    return _spread(diffs)


def _spread_cell(sp: Spread) -> str:
    t = "" if sp.t_stat is None else f" (t {sp.t_stat:+.1f}, {sp.dates} dates)"
    return f"{_f(sp.mean)} pp{t}"


def one_per_stock_month(rows: Sequence[StudyRow]) -> list[StudyRow]:
    """The first setup of each strategy, stock and calendar month: a check that the result is
    not carried by one base counted on many consecutive days."""
    seen: set[tuple[str, str, int, int]] = set()
    out: list[StudyRow] = []
    for r in sorted(rows, key=lambda r: r.day):
        k = (r.strategy, r.instrument_id, r.day.year, r.day.month)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def coverage(rows: Sequence[StudyRow], name: str) -> float | None:
    return _rate([r.keys.get(name) is not None for r in rows]) if rows else None


def _f(x: float | None, digits: int = 2, pct: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.0f} %" if pct else f"{x:+.{digits}f}" if digits else f"{x:.0f}"


def _ic_line(rows: Sequence[StudyRow], key: Key) -> str:
    cells = []
    for h in HORIZONS:
        ic = information_coefficient(rows, key, h)
        t = "" if ic.t_stat is None else f" (t {ic.t_stat:+.1f})"
        cells.append(f"{_f(ic.mean, 3)}{t}")
    return " | ".join(cells)


def format_key(rows: Sequence[StudyRow], name: str, label: str) -> list[str]:
    key = key_of(name)
    out = [f"#### {label}", "",
           f"Coverage: {_f(coverage(rows, name), pct=True)} of {len(rows)} setups.", "",
           "| Quintile (within date) | Setups | 5d | 10d | 20d | 40d | 60d | Median 60d |"
           " Breakout ≤20d | Failed |",
           "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for b in quintiles(rows, key):
        out.append(f"| Q{b.bucket} | {b.n} | " + " | ".join(_f(b.mean[h]) for h in HORIZONS)
                   + f" | {_f(b.median_60)} | {_f(b.breakout_rate, pct=True)}"
                   f" | {_f(b.failed_rate, pct=True)} |")  # fmt: skip
    out += ["", "IC (Spearman within date, mean over dates) at 5 / 10 / 20 / 40 / 60 sessions: "
            + _ic_line(rows, key)]  # fmt: skip
    dedup = one_per_stock_month(rows)
    out += [f"One setup per stock and month ({len(dedup)} setups): " + _ic_line(dedup, key)]
    out += ["Top fifth minus bottom fifth within date, 20d / 60d: "
            + _spread_cell(top_minus_bottom(rows, key, 20)) + " / "
            + _spread_cell(top_minus_bottom(rows, key, 60)), ""]  # fmt: skip
    return out


def format_group(title: str, groups: Mapping[str, Sequence[StudyRow]]) -> list[str]:
    out = [f"| {title} | Setups | 5d | 10d | 20d | 40d | 60d | Median 60d | Breakout ≤20d |"
           " Failed |", "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for label, rows in groups.items():
        b = summarize(rows)
        out.append(f"| {label} | {b.n} | " + " | ".join(_f(b.mean[h]) for h in HORIZONS)
                   + f" | {_f(b.median_60)} | {_f(b.breakout_rate, pct=True)}"
                   f" | {_f(b.failed_rate, pct=True)} |")  # fmt: skip
    return out


PART_LABELS = {
    "m_eps_yoy": "EPS growth YoY", "m_eps_qoq": "EPS growth QoQ", "m_sales_yoy": "Sales growth YoY",
    "m_eps_acceleration": "EPS acceleration", "m_margin_expansion": "Margin change YoY",
    "m_roe": "ROE", "m_debt": "Debt / equity (lower is better: expect a negative IC)",
}  # fmt: skip
SHAREHOLDING_LABELS = {
    "promoter_change_1q": "Promoter change over the quarter (pp)",
    "fii_change_1q": "FII change over the quarter (pp)",
    "dii_change_1q": "DII change over the quarter (pp)",
    "pledge_rising": "Pledge rose over the quarter (1 = yes)",
}


def _ic_table(groups: Mapping[str, Sequence[StudyRow]], keys: Mapping[str, str]) -> list[str]:
    out = ["| Key | Strategy | Coverage | IC 20d | IC 60d |", "|---|---|---|---|---|"]
    for name, label in keys.items():
        key = key_of(name)
        for g, rows in groups.items():
            cells = []
            for h in (20, 60):
                ic = information_coefficient(rows, key, h)
                t = "" if ic.t_stat is None else f" (t {ic.t_stat:+.1f}, {ic.dates} dates)"
                cells.append(f"{_f(ic.mean, 3)}{t}")
            out.append(f"| {label} | {g} | {_f(coverage(rows, name), pct=True)} | "
                       + " | ".join(cells) + " |")  # fmt: skip
    return out


def build_report(
    rows: Sequence[StudyRow], period: str, start: date, end: date, version: str,
    hashes: Mapping[str, str],
) -> str:  # fmt: skip
    strategies = sorted({r.strategy for r in rows})
    groups: dict[str, list[StudyRow]] = {
        s: [r for r in rows if r.strategy == s] for s in strategies
    }
    groups["all strategies"] = list(rows)
    frozen = ", ".join(f"{k} {v[:8]}" for k, v in sorted(hashes.items()))
    out = [
        f"# F7 fundamentals study: {period} period ({start} to {end})", "",
        "FUNDAMENTALS_SPECIFICATION §12. A report only: no weight or bound was fitted, nothing",
        "in the scan, score, backtest or paper ledger changes (§2).", "",
        f"* Setups: {len(rows)} labelled setups of {len(strategies)} strategies, each strategy's"
        f" frozen config only ({frozen}).",
        f"* Shadow score: SCORING_SPECIFICATION §7 as written, version `{version}`; point in time"
        " (the view stored as of the scan day's close: only filings broadcast by then).",
        "* Quintiles are ranked within each scan date (a strong or weak week lifts all buckets"
        " alike); returns are mean % from the scan close; Q5 = highest key.",
        "* IC: Spearman correlation within each scan date (dates with at least"
        f" {MIN_PER_DATE} setups), averaged over dates; t = mean / sd × √dates.",
        "* Shareholding keys are exploratory (§15): not part of §7, listed for the review only.",
        "",
        "## 1. Coverage: setups with and without a score", "",
    ]  # fmt: skip
    for g, grs in groups.items():
        with_s = [r for r in grs if r.keys.get("shadow_score") is not None]
        no_view = [r for r in grs if r.keys.get("availability") is None]
        thin = [r for r in grs if r.keys.get("availability") is not None
                and r.keys.get("shadow_score") is None]  # fmt: skip
        out += [f"### {g}", ""] + format_group("Group", {
            "Scored": with_s, "Results filed, too few metrics": thin, "No results filed": no_view,
        }) + [""]  # fmt: skip
    out += ["## 2. Shadow score", ""]
    for g, grs in groups.items():
        out += format_key(grs, "shadow_score", f"{g}: shadow score")
    out += ["## 3. The seven parts", ""] + _ic_table(groups, PART_LABELS) + [""]
    out += ["## 4. Hard gate (negative TTM EPS or too little data)", ""]
    for g, grs in groups.items():
        out += [f"### {g}", ""] + format_group("Gate", {
            "Pass": [r for r in grs if r.keys.get("gate_pass") == 1.0],
            "Fail": [r for r in grs if r.keys.get("gate_pass") == 0.0],
        }) + ["", "Pass minus fail within date (the table above mixes periods), 20d / 60d: "
              + _spread_cell(flag_within_date(grs, "gate_pass", 20)) + " / "
              + _spread_cell(flag_within_date(grs, "gate_pass", 60)), ""]  # fmt: skip
    out += ["## 5. Shareholding (exploratory)", ""] + _ic_table(groups, SHAREHOLDING_LABELS) + [""]
    for g in ("vcp", "all strategies"):
        if g in groups:
            out += format_key(groups[g], "fii_change_1q", f"{g}: FII change over the quarter")
    return "\n".join(out) + "\n"
