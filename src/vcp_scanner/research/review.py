"""Mark-check review sheet (Phase 6 validation, option A).

Labelling charts from scratch is hard and a wrong label could bias the detector. This sheet asks
an easier question: the detector's own marks are drawn on each chart (base start, contraction
peaks and troughs with depths, the pivot line, a one-line summary) and the reviewer answers "are
these marks in sensible places?" with yes / partly / no / unsure plus optional notes. The answers
are a check on the detector's *geometry*; they are not training labels and no threshold is fitted
to them (VCP_SPECIFICATION 57).

Windows are Trend Template passers drawn with ``labelling.collect_candidates`` / ``sample`` and
then cut to per-stratum quotas weighted towards VCP-class windows (``QUOTAS``). Outputs:
``review_windows.json`` (id, symbol, date, detector answer and marks) and ``review_sheet.html``
(progress kept in the browser, "Download CSV" exports ``id,window,label,notes``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from vcp_scanner.config.models import ClassificationConfig, VCPThresholdsConfig
from vcp_scanner.domain.enums import VCPClassification
from vcp_scanner.domain.vcp import VCPPattern
from vcp_scanner.patterns.vcp.detector import VCPDetector
from vcp_scanner.research.labelling import CHART_BARS, Candidate, sample

QUOTAS: dict[str, int] = {
    "A_PLUS_VCP": 4,
    "NEAR_A_PLUS": 8,
    "VCP": 12,
    "VCP_LIKE": 10,
    "FAILED": 5,
    "NONE": 5,
    "NO_PATTERN": 0,
}

REVIEW_OPTIONS = (
    '[["", "-- marks? --"], ["yes", "Yes, sensible"], ["partly", "Partly"], '
    '["no", "No, wrong"], ["unsure", "Unsure"]]'
)

REVIEW_HELP = """<dl class="help">
  <dt>What to check</dt>
  <dd>Each chart ends at the window's date. The detector's marks are drawn on it. You are not
      asked whether this is a VCP; only whether the marks sit in sensible places.</dd>
  <dt>Marks</dt>
  <dd>Grey dashed line = where the detector thinks the base starts. Orange dots T1, T2 ... =
      contraction peaks; open circles with a % = the low of each pullback and its depth. Purple
      dashed line = the pivot (buy point). The line above the chart is the detector's verdict.</dd>
  <dt>Yes</dt><dd>Peaks and lows are on the obvious swing highs and lows, the base start is
      roughly where the sideways action began, the pivot is near the top of the recent tight
      area.</dd>
  <dt>Partly</dt><dd>Mostly right, but one mark is off (a missed or extra contraction, base start
      too early or late, pivot at the wrong level). Say which in the notes if you can.</dd>
  <dt>No</dt><dd>The marks do not describe what the chart shows.</dd>
  <dt>Unsure</dt><dd>You cannot tell. That is a fine answer.</dd>
  <dt>Saving</dt><dd>Answers are kept in this browser. Use Download CSV when done and send the
      file back.</dd>
</dl>"""


_PLAIN = {
    "min_contractions": "too few contractions",
    "max_contractions": "too many contractions",
    "max_final_contraction_pct": "last contraction too deep",
    "require_tight_pivot": "right side not tight",
    "require_volume_dryup": "no volume dry-up",
    "require_volatility_contraction": "volatility not contracting",
    "require_progressive_tightening": "not tightening",
}


def _idx(dates: Sequence[str], d: date | None) -> int:
    """Position of ``d`` in the chart's dates, -1 when absent or off the chart."""
    if d is None:
        return -1
    try:
        return list(dates).index(d.isoformat())
    except ValueError:
        return -1


def marks(pattern: VCPPattern | None, chart_dates: Sequence[str], summary: str) -> dict[str, Any]:
    """Marks for the sheet: indices relative to ``chart_dates`` (-1 = off the chart)."""
    if pattern is None:
        return {"bs": -1, "t": [], "pv": None, "txt": summary}
    t = [
        {
            "p": _idx(chart_dates, c.peak_date),
            "ph": round(c.peak_price, 2),
            "q": _idx(chart_dates, c.trough_date),
            "ql": round(c.trough_price, 2),
            "d": round(c.depth_pct, 1),
        }
        for c in pattern.contractions
    ]
    pv = round(pattern.pivot.pivot_price, 2) if pattern.pivot else None
    return {"bs": _idx(chart_dates, pattern.base_start), "t": t, "pv": pv, "txt": summary}


def summary(det: Any) -> str:
    """One line: class, status, depths, pivot distance, unmet A+ rules."""
    p = det.pattern
    if p is None:
        return f"No pattern ({det.no_pattern_reason or 'no base'})"
    parts = [f"{p.classification.value} · {p.status.value}"]
    if p.contractions:
        parts.append("depths " + " → ".join(f"{c.depth_pct:.1f}%" for c in p.contractions))
    if p.pivot is not None:
        dist = p.pivot.distance_to_close_pct
        parts.append(f"pivot {dist:.1f}% above close" if dist >= 0
                     else f"close {-dist:.1f}% above pivot")  # fmt: skip
    if p.invalidation_reasons:
        parts.append("invalid: " + ", ".join(r.value for r in p.invalidation_reasons))
    if det.classification is not None and p.classification is VCPClassification.VCP:
        unmet = det.classification.unmet.get(VCPClassification.A_PLUS_VCP, ())
        if unmet:
            parts.append("not A+: " + ", ".join(_PLAIN.get(u, u) for u in unmet))
    return " · ".join(parts)


def select(
    candidates: Sequence[Candidate], seed: int, quotas: Mapping[str, int] = QUOTAS
) -> list[Candidate]:
    """``labelling.sample`` (diverse, deterministic, shuffled) cut to per-stratum quotas."""
    pool = sample(candidates, max(quotas.values(), default=0), seed)
    taken: dict[str, int] = {}
    out: list[Candidate] = []
    for c in pool:
        if taken.get(c.stratum, 0) < quotas.get(c.stratum, 0):
            out.append(c)
            taken[c.stratum] = taken.get(c.stratum, 0) + 1
    return out


def build_windows(
    chosen: Sequence[Candidate], vcp: VCPThresholdsConfig, classification: ClassificationConfig,
    config_hash: str,
) -> list[dict[str, Any]]:  # fmt: skip
    """Rerun the detector on each chosen window and attach chart data and marks."""
    det = VCPDetector(vcp, classification, config_hash=config_hash)
    out: list[dict[str, Any]] = []
    for k, c in enumerate(chosen):
        r = det.detect(c.instrument_id, c.series, c.as_of, trend_template_pass=True,
                       weekly_stage2_pass=c.weekly_stage2_pass)  # fmt: skip
        s = c.series
        d = [x.isoformat() for x in s.dates[-CHART_BARS:]]
        out.append({
            "id": c.id, "n": k + 1, "sym": c.symbol, "asof": c.as_of.isoformat(),
            "stratum": c.stratum, "detector": c.detector,
            "d": d,
            "h": [round(x, 2) for x in s.high[-CHART_BARS:]],
            "l": [round(x, 2) for x in s.low[-CHART_BARS:]],
            "c": [round(x, 2) for x in s.close[-CHART_BARS:]],
            "v": [None if x is None else round(x) for x in s.volume[-CHART_BARS:]],
            "m": marks(r.pattern, d, summary(r)),
        })  # fmt: skip
    return out


def write_review(
    windows: Sequence[dict[str, Any]], out_dir: Path, *, title: str
) -> dict[str, Path]:
    """Write review_windows.json (no bars) and review_sheet.html into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"windows": out_dir / "review_windows.json", "sheet": out_dir / "review_sheet.html"}
    meta = [
        {k: w[k] for k in ("id", "n", "sym", "asof", "stratum", "detector", "m")} for w in windows
    ]
    paths["windows"].write_text(json.dumps(meta, indent=1))
    html = (
        _SHEET.replace("__TITLE__", title)
        .replace("__HELP__", REVIEW_HELP)
        .replace("__OPTIONS__", REVIEW_OPTIONS)
        .replace("__DATA__", json.dumps(list(windows), separators=(",", ":")))
    )
    paths["sheet"].write_text(html)
    return paths


from vcp_scanner.research._sheet_template import SHEET as _SHEET  # noqa: E402
