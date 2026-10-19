"""Shadow fundamental score for the F7 study (FUNDAMENTALS_SPECIFICATION §12).

SCORING_SPECIFICATION §7 applied to the point-in-time metrics, exactly as written: the weights
and worst -> best bounds of ``FundamentalScoreComponents`` (no tuning), a missing metric drops
out and the other weights are renormalised, and fewer than ``min_availability`` of the seven
metrics present gives no score. Stored only in ``fundamental_research_scores``; nothing in the
scan, score, backtest or paper ledger reads it (§2 freeze rule).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from vcp_scanner.config.loader import compute_config_hash
from vcp_scanner.config.models import FundamentalScoreComponents
from vcp_scanner.scoring.components import linear

#: The seven §7 metrics and the ``fundamental_metrics`` column each one reads.
METRIC_COLUMNS: Mapping[str, str] = {
    "eps_yoy": "eps_yoy",
    "eps_qoq": "eps_qoq",
    "sales_yoy": "revenue_yoy",
    "eps_acceleration": "eps_acceleration",
    "margin_expansion": "margin_expansion",
    "roe": "roe",
    "debt": "debt_to_equity",
}
VERSION_PREFIX = "shadow-1"


@dataclass(frozen=True, slots=True)
class ShadowScore:
    score: float | None  # 0..100; None: fewer than ``min_availability`` of the metrics
    availability: float  # share of the seven metrics present, 0..1
    parts: dict[str, float]  # metric -> its 0..100 sub-score (present metrics only)


def score_version(comps: FundamentalScoreComponents, min_availability: float) -> str:
    """``shadow-1-<hash>``: changes whenever a weight, a bound or the availability floor does."""
    h = compute_config_hash({"c": comps.model_dump(), "min": min_availability})
    return f"{VERSION_PREFIX}-{h[:12]}"


def shadow_score(
    metrics: Mapping[str, float | None],
    comps: FundamentalScoreComponents,
    min_availability: float,
) -> ShadowScore:
    """The §7 score of one stock on one day from its metrics (keys of ``METRIC_COLUMNS``)."""
    parts: dict[str, float] = {}
    for name in METRIC_COLUMNS:
        x = metrics.get(name)
        if x is None or not math.isfinite(x):
            continue
        b = comps.bounds[name]
        parts[name] = linear(float(x), b.worst, b.best)
    availability = len(parts) / len(METRIC_COLUMNS)
    if not parts or availability < min_availability:
        return ShadowScore(None, availability, parts)
    weight = sum(comps.weights[n] for n in parts)
    score = sum(comps.weights[n] * v for n, v in parts.items()) / weight
    return ShadowScore(round(score, 6), availability, parts)


_VIEW_SQL = f"""
WITH asked AS (SELECT * FROM (SELECT unnest(?) AS instrument_id, unnest(?) AS day))
SELECT a.instrument_id, a.day, v.as_of_date, v.period_end,
       {", ".join(f"v.{c}" for c in METRIC_COLUMNS.values())}, d.fundamental_hard_gate_pass
FROM asked a
LEFT JOIN fundamental_metrics v
       ON v.instrument_id = a.instrument_id AND v.as_of_date <= a.day
LEFT JOIN fundamental_data_quality d
       ON d.instrument_id = v.instrument_id AND d.as_of_date = v.as_of_date
QUALIFY row_number() OVER (PARTITION BY a.instrument_id, a.day
                           ORDER BY v.as_of_date DESC NULLS LAST) = 1
"""


@dataclass(frozen=True, slots=True)
class PointInTime:
    instrument_id: str
    day: date
    view_date: date | None  # the stored view used (None: no results filed by ``day``)
    period_end: date | None
    metrics: dict[str, float | None]
    gate_pass: bool | None
    shadow: ShadowScore


def scores_as_of(
    conn: Any,
    pairs: Sequence[tuple[str, date]],
    comps: FundamentalScoreComponents,
    min_availability: float,
) -> list[PointInTime]:
    """The shadow score of each (instrument, day) from the view stored as of that day: only
    filings available by the day's close (the view's ``as_of_date``, §7)."""
    if not pairs:
        return []
    ids = [p[0] for p in pairs]
    days = [p[1] for p in pairs]
    out: list[PointInTime] = []
    names = list(METRIC_COLUMNS)
    for r in conn.execute(_VIEW_SQL, [ids, days]).fetchall():
        metrics = {n: (None if r[4 + k] is None else float(r[4 + k])) for k, n in enumerate(names)}
        out.append(PointInTime(
            instrument_id=str(r[0]), day=r[1], view_date=r[2], period_end=r[3], metrics=metrics,
            gate_pass=None if r[4 + len(names)] is None else bool(r[4 + len(names)]),
            shadow=shadow_score(metrics, comps, min_availability),
        ))  # fmt: skip
    return out


def store_scores(conn: Any, rows: Sequence[PointInTime], version: str) -> int:
    """Write the scores to ``fundamental_research_scores`` (replacing this version's rows for
    the same stock and day). Returns the number written."""
    data = [
        (p.instrument_id, p.day, version, p.shadow.score,
         json.dumps({"availability": p.shadow.availability, "parts": p.shadow.parts,
                     "view_date": None if p.view_date is None else p.view_date.isoformat(),
                     "period_end": None if p.period_end is None else p.period_end.isoformat(),
                     "gate_pass": p.gate_pass}, sort_keys=True))
        for p in rows
    ]  # fmt: skip
    if not data:
        return 0
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _frs AS SELECT * FROM fundamental_research_scores LIMIT 0"
    )
    conn.execute("DELETE FROM _frs")
    conn.executemany("INSERT INTO _frs VALUES (?, ?, ?, ?, ?)", data)
    conn.execute(
        "DELETE FROM fundamental_research_scores f USING _frs t WHERE f.instrument_id ="
        " t.instrument_id AND f.as_of_date = t.as_of_date AND f.score_version = t.score_version"
    )
    conn.execute("INSERT INTO fundamental_research_scores SELECT * FROM _frs")
    return len(data)
