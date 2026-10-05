"""Chart review sheet for file-configured strategies (decision D6; STRATEGY_SPEC 14.9 C1).

Like the VCP mark check (``research/review.py``): the detector's own marks are drawn on each chart
(base start, pivot line, stop line, the breakout day) with a one-line summary, and the reviewer
answers yes / partly / no / unsure. The answers check the detector against the owner's eye; no
threshold is fitted to them.

Windows come from **stored** setups (``strategy_setups`` of one strategy and config) in a date
range, normally the development period. Sample (decision C1): 12 grade 2, 8 grade 3 (all if
fewer), 6 grade 1 and 4 near-misses (grade 1 setups that miss grade 2 by exactly one rule),
spread over the years round-robin, one window per instrument, deterministic for a seed.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.research.labelling import CHART_BARS
from vcp_scanner.research.review import write_review

QUOTAS: dict[str, int] = {"GRADE_2": 12, "GRADE_3": 8, "GRADE_1": 6, "NEAR_MISS": 4}
_TREND_RULE = "trend_template_and_stage2"
#: Stored point dates drawn as labelled dots: details key -> (label, price, label above).
POINTS: dict[str, tuple[str, str, bool]] = {
    "left_lip_date": ("L", "h", True), "bottom_date": ("B", "l", False),
    "right_lip_date": ("R", "h", True), "handle_low_date": ("H", "l", False),
    "left_high_date": ("L", "h", True), "first_low_date": ("B1", "l", False),
    "middle_peak_date": ("M", "h", True), "second_low_date": ("B2", "l", False),
}  # fmt: skip
#: Bars shown before the base start, so the advance into it is visible.
LEAD_BARS = 40

HELP = """<dl class="help">
  <dt>What to check</dt>
  <dd>Each chart ends at the window's date. The detector's marks are drawn on it. The question is
      whether the marks describe a sensible __NAME__, not whether you would buy it.</dd>
  <dt>Marks</dt>
  <dd>Grey dashed line = where the base / pattern starts. Purple dashed line = the pivot (buy
      point). Red dashed line = the stop level. A green dot = the breakout day, when there is one.
      Blue dots = the pattern's points (cup: L left lip, B bottom, R right lip / handle start,
      H handle low; double bottom: L left high, B1 first low, M middle peak, B2 second low).
      The line above the chart is the detector's verdict.</dd>
  <dt>Yes</dt><dd>The base start, pivot and stop sit where you would put them.</dd>
  <dt>Partly</dt><dd>Mostly right, but one mark is off. Say which in the notes.</dd>
  <dt>No</dt><dd>The marks do not describe what the chart shows.</dd>
  <dt>Unsure</dt><dd>You cannot tell. That is a fine answer.</dd>
  <dt>Saving</dt><dd>Answers are kept in this browser. Use Download CSV when done and send the
      file back.</dd>
</dl>"""

OPTIONS = (
    '[["", "-- marks? --"], ["yes", "Yes, sensible"], ["partly", "Partly"], '
    '["no", "No, wrong"], ["unsure", "Unsure"]]'
)


def stratum(grade: int, unmet: Mapping[str, Sequence[str]], grade2_tier: str) -> str:
    if grade >= 3:
        return "GRADE_3"
    if grade == 2:
        return "GRADE_2"
    missing = [r for r in unmet.get(grade2_tier, ()) if r != _TREND_RULE]
    return "NEAR_MISS" if len(missing) == 1 else "GRADE_1"


def load_setups(
    store: DuckDBStore, strategy_id: str, config_hash: str, start: date, end: date
) -> list[dict[str, Any]]:
    cols = ("instrument_id", "as_of_date", "base_start_date", "base_end_date", "pivot_price",
            "stop_reference_price", "classification", "grade", "status", "base_depth_pct",
            "pivot_distance_pct", "unmet_rules", "details_json", "symbol")  # fmt: skip
    rows = store.conn.execute(
        """
        SELECT s.instrument_id, s.as_of_date, s.base_start_date, s.base_end_date, s.pivot_price,
               s.stop_reference_price, s.classification, s.grade, s.status, s.base_depth_pct,
               s.pivot_distance_pct, s.unmet_rules, s.details_json,
               coalesce(i.symbol, s.instrument_id)
        FROM strategy_setups s LEFT JOIN instruments i USING (instrument_id)
        WHERE s.strategy_id = ? AND s.config_hash = ? AND s.is_primary
          AND s.as_of_date BETWEEN ? AND ? AND s.data_snapshot_id = ?
        ORDER BY s.as_of_date, s.instrument_id
        """,
        [strategy_id, config_hash, start, end, LIVE_SNAPSHOT_ID],
    ).fetchall()
    out = []
    for r in rows:
        d = dict(zip(cols, r, strict=True))
        d["unmet_rules"] = json.loads(d["unmet_rules"] or "{}")
        d["details"] = json.loads(d.pop("details_json") or "{}")
        out.append(d)
    return out


def select(
    setups: Sequence[dict[str, Any]], grade2_tier: str, seed: int,
    quotas: Mapping[str, int] = QUOTAS,
) -> list[dict[str, Any]]:  # fmt: skip
    """Per stratum, round-robin over the years in random (seeded) order; one window per
    instrument overall."""
    rng = random.Random(seed)
    by: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for s in setups:
        s = {**s, "stratum": stratum(s["grade"], s["unmet_rules"], grade2_tier)}
        by[s["stratum"]][s["as_of_date"].year].append(s)
    used: set[str] = set()
    chosen: list[dict[str, Any]] = []
    for name, quota in quotas.items():
        years = {y: rng.sample(v, len(v)) for y, v in sorted(by[name].items())}
        taken = 0
        while taken < quota and any(years.values()):
            for y in sorted(years):
                while years[y] and years[y][-1]["instrument_id"] in used:
                    years[y].pop()
                if years[y] and taken < quota:
                    s = years[y].pop()
                    used.add(s["instrument_id"])
                    chosen.append(s)
                    taken += 1
    rng.shuffle(chosen)
    return chosen


def summary(s: Mapping[str, Any], grade2_tier: str) -> str:
    parts = [f"{s['classification']} · {s['status']}", f"depth {s['base_depth_pct']:.1f}%"]
    det = s["details"]
    if "weekly_close_range_pct" in det and det["weekly_close_range_pct"] is not None:
        parts.append(f"weekly closes within {det['weekly_close_range_pct']:.1f}%, "
                     f"{det.get('weeks')} weeks")  # fmt: skip
    if "handle_depth_pct" in det:
        parts.append(f"cup {det.get('cup_weeks')} weeks, handle {det['handle_depth_pct']:.1f}% "
                     f"deep over {det.get('handle_sessions')} sessions, bottom share "
                     f"{det.get('bottom_share', 0):.2f}")  # fmt: skip
    if "undercut_pct" in det:
        parts.append(f"undercut {det['undercut_pct']:.1f}%, middle bounce "
                     f"{det.get('middle_bounce_pct', 0):.1f}%")  # fmt: skip
    if "max_close_change_pct" in det:
        parts.append(f"largest weekly close change {det['max_close_change_pct']:.2f}%, "
                     f"{det.get('tight_weeks')} tight weeks")  # fmt: skip
    dist = s["pivot_distance_pct"]
    if dist is not None:
        parts.append(f"pivot {dist:.1f}% above close" if dist >= 0
                     else f"close {-dist:.1f}% above pivot")  # fmt: skip
    unmet = [r for r in s["unmet_rules"].get(grade2_tier, ()) if s["grade"] < 2]
    if unmet:
        parts.append("not " + grade2_tier + ": " + ", ".join(unmet))
    return " · ".join(parts)


def build_windows(
    store: DuckDBStore, chosen: Sequence[dict[str, Any]], grade2_tier: str, strategy_id: str
) -> list[dict[str, Any]]:
    out = []
    for k, s in enumerate(chosen):
        # Long bases (cups up to 65 weeks) need more than the usual chart length.
        days = max(420, (s["as_of_date"] - s["base_start_date"]).days + 2 * LEAD_BARS)
        bars = store.conn.execute(
            "SELECT trade_date, high_adj, low_adj, close_adj, volume_adj"
            " FROM daily_prices_adjusted_current WHERE computed_from_snapshot_id = ?"
            " AND instrument_id = ? AND trade_date <= ? AND trade_date > ?"
            " ORDER BY trade_date",
            [LIVE_SNAPSHOT_ID, s["instrument_id"], s["as_of_date"],
             s["as_of_date"] - timedelta(days=days)],
        ).fetchall()  # fmt: skip
        start = next((i for i, b in enumerate(bars) if b[0] >= s["base_start_date"]), 0)
        bars = bars[min(max(0, len(bars) - CHART_BARS), max(0, start - LEAD_BARS)) :]
        d = [b[0].isoformat() for b in bars]
        idx = {x: i for i, x in enumerate(d)}
        pts = []
        for key, (label, col, up) in POINTS.items():
            when = s["details"].get(key)
            if isinstance(when, str) and when in idx:
                bar = bars[idx[when]]
                pts.append({"k": label, "i": idx[when], "up": up,
                            "y": round(bar[1] if col == "h" else bar[2], 2)})  # fmt: skip
        be = s["base_end_date"]
        out.append({
            "id": f"{strategy_id}-{s['instrument_id']}-{s['as_of_date']}", "n": k + 1,
            "sym": s["symbol"], "asof": s["as_of_date"].isoformat(), "stratum": s["stratum"],
            "detector": {"classification": s["classification"], "status": s["status"]},
            "d": d, "h": [round(b[1], 2) for b in bars], "l": [round(b[2], 2) for b in bars],
            "c": [round(b[3], 2) for b in bars],
            "v": [None if b[4] is None else round(b[4]) for b in bars],
            "m": {"bs": idx.get(s["base_start_date"].isoformat(), -1), "t": [],
                  "pv": None if s["pivot_price"] is None else round(s["pivot_price"], 2),
                  "sl": None if s["stop_reference_price"] is None
                  else round(s["stop_reference_price"], 2),
                  "bo": idx.get(be.isoformat(), -1) if be else -1,
                  "pts": pts, "txt": summary(s, grade2_tier)},
        })  # fmt: skip
    return out


def write(
    windows: Sequence[dict[str, Any]], out_dir: Path, title: str, name: str
) -> dict[str, Path]:
    help_html = HELP.replace("__NAME__", name)
    return write_review(windows, out_dir, title=title, help_html=help_html, options=OPTIONS)
