"""``what_if_score`` (AGENT_SPECIFICATION §3 tier 1, step A2b): re-score a strategy's stored
setups under changed scoring weights or bounds and compare how well each ranking orders the
outcomes. Read-only, research only: the frozen config and the stored scores are not touched.

Every stored score keeps its sub-components (``score_components``: raw measurement, normalized
value, weight within the component), so a score is recomputed exactly as the scorer does
(SCORING_SPECIFICATION 1-6, ``scoring/components.py`` and ``final.py``):

* sub-component normalized = the stored value, or ``linear(raw, worst, best)`` when the change
  sets new bounds for it; a NULL sub-component stays NULL;
* component = sum(normalized x weight) / sum(weights of the available sub-components);
* final = sum(component weight x component) / sum(weights of the available components).

Before comparing, the unchanged recompute must reproduce the stored final scores (``baseline
check``); if it does not, the result is refused rather than reported on a wrong base.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from vcp_scanner.scoring.components import linear

COMPONENT_KEYS = {"trend": "TREND", "pattern": "PATTERN", "volume": "VOLUME", "rs": "RS"}
TOLERANCE = 0.01  # score points
MIN_MATCH = 0.99  # share of setups the baseline must reproduce
TOP_N = 5


@dataclass(slots=True)
class Scored:
    instrument_id: str
    day: date
    stored_final: float | None
    ret: float | None
    # component -> sub -> (raw, stored normalized, weight)
    subs: dict[str, dict[str, tuple[float | None, float | None, float]]] = field(
        default_factory=lambda: defaultdict(dict)
    )


@dataclass(frozen=True, slots=True)
class Change:
    weights: dict[str, float]  # component (TREND, PATTERN, VOLUME, RS) -> weight
    sub_weights: dict[tuple[str, str], float]  # (component, sub) -> weight
    bounds: dict[tuple[str, str], tuple[float, float]]  # (component, sub) -> (worst, best)


def _key(name: str) -> tuple[str, str]:
    comp, _, sub = name.partition(".")
    comp_u = COMPONENT_KEYS.get(comp.lower(), comp.upper())
    if comp_u == "VCP":
        comp_u = "PATTERN"
    if not sub:
        raise ValueError(f"{name!r}: write component.sub_component, e.g. volume.dryup_quality")
    return comp_u, sub.lower()


def parse_change(weights: dict[str, Any] | None, sub_weights: dict[str, Any] | None,
                 bounds: dict[str, Any] | None) -> Change:  # fmt: skip
    w: dict[str, float] = {}
    for k, v in (weights or {}).items():
        comp = COMPONENT_KEYS.get(k.lower())
        if comp is None:
            raise ValueError(f"weights keys are {', '.join(COMPONENT_KEYS)}; got {k!r}")
        if float(v) < 0:
            raise ValueError("weights must be >= 0")
        w[comp] = float(v)
    sw = {}
    for k, v in (sub_weights or {}).items():
        if float(v) < 0:
            raise ValueError("sub_weights must be >= 0")
        sw[_key(k)] = float(v)
    b = {}
    for k, v in (bounds or {}).items():
        if not isinstance(v, dict) or "worst" not in v or "best" not in v:
            raise ValueError(f"bounds[{k!r}] must be {{'worst': x, 'best': y}}")
        worst, best = float(v["worst"]), float(v["best"])
        if worst == best:
            raise ValueError(f"bounds[{k!r}]: worst and best must differ")
        b[_key(k)] = (worst, best)
    if not (w or sw or b):
        raise ValueError("no change given: set weights, sub_weights or bounds")
    return Change(w, sw, b)


def load(research: Any, strategy: str, config_hash: str, start: date, end: date,
         horizon: int, eligible_only: bool = True) -> list[Scored]:  # fmt: skip
    rows = research.execute(
        f"""
        WITH s AS (
            SELECT scan_id, instrument_id, as_of_date, final_setup_score, eligible
            FROM setup_scores
            WHERE strategy_id = ? AND config_hash = ? AND data_snapshot_id = 'LIVE'
              AND as_of_date BETWEEN ? AND ?
            QUALIFY row_number() OVER (PARTITION BY instrument_id, as_of_date
                                       ORDER BY created_at DESC) = 1
        )
        SELECT s.instrument_id, s.as_of_date, s.final_setup_score, f.ret_{int(horizon)},
               c.component, c.sub_component, c.raw_measurement, c.normalized_0_100,
               c.weight_within_component
        FROM s
        JOIN score_components c
          ON c.scan_id = s.scan_id AND c.instrument_id = s.instrument_id
         AND c.strategy_id = ?
        LEFT JOIN forward_labels f
          ON f.strategy_id = ? AND f.instrument_id = s.instrument_id
         AND f.as_of_date = s.as_of_date AND f.config_hash = ?
        WHERE {"s.eligible" if eligible_only else "TRUE"}
        """,
        [strategy, config_hash, start, end, strategy, strategy, config_hash],
    ).fetchall()
    by: dict[tuple[str, date], Scored] = {}
    for iid, day, final, ret, comp, sub, raw, norm, weight in rows:
        k = (str(iid), day)
        s = by.get(k)
        if s is None:
            s = by[k] = Scored(str(iid), day, None if final is None else float(final),
                               None if ret is None else float(ret))  # fmt: skip
        comp_u = "PATTERN" if str(comp) == "VCP" else str(comp)
        s.subs[comp_u][str(sub)] = (
            None if raw is None or not math.isfinite(float(raw)) else float(raw),
            None if norm is None else float(norm), float(weight),
        )  # fmt: skip
    return list(by.values())


def recompute(s: Scored, base_weights: dict[str, float], change: Change | None) -> float | None:
    comp_scores: dict[str, float] = {}
    for comp, subs in s.subs.items():
        num = den = 0.0
        for sub, (raw, norm, weight) in subs.items():
            key = (comp, sub)
            if change and key in change.bounds and raw is not None:
                norm = linear(raw, *change.bounds[key])
            elif change and key in change.bounds and raw is None:
                norm = None
            if norm is None:
                continue
            w = change.sub_weights.get(key, weight) if change else weight
            num += norm * w
            den += w
        if den > 0:
            comp_scores[comp] = num / den
    weights = {**base_weights, **(change.weights if change else {})}
    avail = {k: w for k, w in weights.items() if k in comp_scores and w > 0}
    total = sum(avail.values())
    return None if total <= 0 else sum(w * comp_scores[k] for k, w in avail.items()) / total


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return None if den == 0 else num / den


def evaluate(scored: list[tuple[Scored, float]], top_n: int = TOP_N) -> dict[str, Any]:
    """How a score orders the outcomes, within each scan day (so market moves cancel)."""
    by_day: dict[date, list[tuple[float, float]]] = defaultdict(list)
    for s, score in scored:
        if s.ret is not None:
            by_day[s.day].append((score, s.ret))
    ics, spreads, tops, alls = [], [], [], []
    for pairs in by_day.values():
        if len(pairs) >= 5:
            ic = spearman([p[0] for p in pairs], [p[1] for p in pairs])
            if ic is not None:
                ics.append(ic)
            ordered = sorted(pairs, key=lambda p: p[0])
            q = max(1, len(ordered) // 5)
            spreads.append(statistics.fmean(p[1] for p in ordered[-q:])
                           - statistics.fmean(p[1] for p in ordered[:q]))  # fmt: skip
        best = sorted(pairs, key=lambda p: -p[0])[:top_n]
        tops += [p[1] for p in best]
        alls += [p[1] for p in pairs]
    ic_mean = statistics.fmean(ics) if ics else None
    ic_t = None
    if ic_mean is not None and len(ics) > 2 and statistics.stdev(ics) > 0:
        ic_t = ic_mean / (statistics.stdev(ics) / math.sqrt(len(ics)))

    def r(x: float | None, d: int = 3) -> float | None:
        return None if x is None else round(x, d)

    return {
        "days": len(by_day), "setups_with_outcome": len(alls), "days_ranked": len(ics),
        "ic_mean": r(ic_mean), "ic_t": r(ic_t, 2),
        "top_minus_bottom_fifth_pct": r(statistics.fmean(spreads) if spreads else None, 2),
        f"top_{top_n}_per_day_mean_pct": r(statistics.fmean(tops) if tops else None, 2),
        "all_mean_pct": r(statistics.fmean(alls) if alls else None, 2),
    }  # fmt: skip


def compare(rows: list[Scored], base_weights: dict[str, float], change: Change,
            top_n: int = TOP_N) -> dict[str, Any]:  # fmt: skip
    base, new, matched, checked = [], [], 0, 0
    for s in rows:
        b = recompute(s, base_weights, None)
        n = recompute(s, base_weights, change)
        if b is None or n is None:
            continue
        if s.stored_final is not None:
            checked += 1
            matched += abs(b - s.stored_final) <= TOLERANCE
        base.append((s, b))
        new.append((s, n))
    share = matched / checked if checked else 0.0
    check = {"setups": checked, "reproduced": matched, "share": round(share, 4)}
    if share < MIN_MATCH:
        raise ValueError(
            f"baseline check failed: the unchanged recompute reproduces {share:.1%} of the "
            f"stored scores (need {MIN_MATCH:.0%}); the stored components or weights do not "
            "match the configured scoring, so a comparison would be on a wrong base"
        )
    # how much the ranking moves: top-N per day kept
    by_day_b: dict[date, list[tuple[float, str]]] = defaultdict(list)
    by_day_n: dict[date, list[tuple[float, str]]] = defaultdict(list)
    for (s, b), (_s, n) in zip(base, new, strict=True):
        by_day_b[s.day].append((b, s.instrument_id))
        by_day_n[s.day].append((n, s.instrument_id))
    kept = total = 0
    for day, items in by_day_b.items():
        tb = {i for _, i in sorted(items, reverse=True)[:top_n]}
        tn = {i for _, i in sorted(by_day_n[day], reverse=True)[:top_n]}
        kept += len(tb & tn)
        total += len(tb)
    rank_corr = spearman([b for _, b in base], [n for _, n in new])
    return {
        "baseline_check": check,
        "baseline": evaluate(base, top_n), "what_if": evaluate(new, top_n),
        "ranking_change": {"score_rank_correlation": None if rank_corr is None
                           else round(rank_corr, 3),
                           f"top_{top_n}_kept_share": round(kept / total, 3) if total else None},
        "mean_score": {"baseline": round(statistics.fmean(b for _, b in base), 2) if base else None,
                       "what_if": round(statistics.fmean(n for _, n in new), 2) if new else None},
    }  # fmt: skip
