"""Do higher setup scores lead to better outcomes? (SCORING_SPECIFICATION 11; VCP 62-63;
Phase 7 step 5.) A report only: no weight or bound is fitted here (owner decision 2026-10-03).

Windows come from ``outcomes.collect_windows`` with scoring on. Two views:

* **Quintiles within each scan date.** A key (the final score or one component) is ranked
  among the windows of the same date that have it, and split into five equal buckets, so a
  strong or weak month lifts every bucket alike (the regime control). Q5 = highest. Per bucket:
  windows, scan-date win rate (+10 % before -7 % in 60 sessions), median 60-session return,
  breakout trades and their average return under the default exit rule.
* **Information coefficient** (section 63): the Spearman rank correlation between the key and
  the 60-session return within each date, averaged over dates, with its t-statistic (mean /
  standard deviation x sqrt(dates)). Dates with fewer than ``MIN_PER_DATE`` windows are skipped.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from vcp_scanner.research.outcomes import WindowRow

BUCKETS = 5
MIN_PER_DATE = 10


@dataclass(frozen=True, slots=True)
class BucketStats:
    bucket: int  # 1 = lowest .. BUCKETS = highest
    n: int
    win_rate: float | None
    median_ret_60: float | None
    trades: int
    trade_avg: float | None


@dataclass(frozen=True, slots=True)
class IC:
    mean: float | None
    t_stat: float | None
    dates: int


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _ranks(xs: Sequence[float]) -> list[float]:
    """Average ranks (1-based; ties share their mean rank)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    return None if va == 0 or vb == 0 else cov / math.sqrt(va * vb)


Key = Callable[[WindowRow], float | None]


def _by_date(rows: Sequence[WindowRow], key: Key) -> dict[date, list[tuple[float, WindowRow]]]:
    out: dict[date, list[tuple[float, WindowRow]]] = defaultdict(list)
    for r in rows:
        k = key(r)
        if r.outcome is not None and k is not None:
            out[r.as_of].append((k, r))
    return out


def quintiles(rows: Sequence[WindowRow], key: Key) -> list[BucketStats]:
    """Per-date buckets of ``key`` (module docstring); empty buckets are omitted."""
    buckets: dict[int, list[WindowRow]] = defaultdict(list)
    for items in _by_date(rows, key).values():
        ranks = _ranks([k for k, _ in items])
        n = len(items)
        for rank, (_, r) in zip(ranks, items, strict=True):
            buckets[min(BUCKETS, int((rank - 1) / n * BUCKETS) + 1)].append(r)
    out = []
    for b in sorted(buckets):
        rs = buckets[b]
        outs = [r.outcome for r in rs if r.outcome is not None]
        trades = [o.trade_ret for o in outs if o.trade_ret is not None]
        out.append(BucketStats(
            b, len(outs), sum(o.result == "WIN" for o in outs) / len(outs) if outs else None,
            _median([o.ret_60 for o in outs]), len(trades),
            sum(trades) / len(trades) if trades else None,
        ))  # fmt: skip
    return out


def information_coefficient(rows: Sequence[WindowRow], key: Key) -> IC:
    ics = []
    for items in _by_date(rows, key).values():
        if len(items) < MIN_PER_DATE:
            continue
        rets = [r.outcome.ret_60 for _, r in items if r.outcome is not None]
        rho = spearman([k for k, _ in items], rets)
        if rho is not None:
            ics.append(rho)
    if not ics:
        return IC(None, None, 0)
    mean = sum(ics) / len(ics)
    if len(ics) < 2:
        return IC(mean, None, len(ics))
    sd = math.sqrt(sum((x - mean) ** 2 for x in ics) / (len(ics) - 1))
    return IC(mean, mean / sd * math.sqrt(len(ics)) if sd > 0 else None, len(ics))


def format_study(rows: Sequence[WindowRow], keys: Sequence[tuple[str, Key]], title: str) -> str:
    def p(x: float | None, scale: float = 1.0, d: int = 1) -> str:
        return "-" if x is None else f"{x * scale:.{d}f}"

    lines = [title]
    for name, key in keys:
        ic = information_coefficient(rows, key)
        lines.append(f"  {name}: IC {p(ic.mean, d=3)} (t {p(ic.t_stat, d=1)}, {ic.dates} dates)")
        lines.append(f"    {'bucket':>6} {'n':>6} {'win%':>6} {'ret60':>6} {'trades':>6} "
                     f"{'trade%':>6}")  # fmt: skip
        for b in quintiles(rows, key):
            q = "Q" + str(b.bucket)
            lines.append(f"    {q:>6} {b.n:6d} {p(b.win_rate, 100):>6} {p(b.median_ret_60):>6}"
                         f" {b.trades:6d} {p(b.trade_avg, d=2):>6}")  # fmt: skip
    return "\n".join(lines)


def standard_keys() -> list[tuple[str, Key]]:
    """Final score and each technical component."""
    keys: list[tuple[str, Key]] = [("final score", lambda r: r.score)]
    for comp in ("TREND", "VCP", "VOLUME", "RS"):
        keys.append((comp.lower(), lambda r, c=comp: r.components.get(c)))  # type: ignore[misc]
    return keys
