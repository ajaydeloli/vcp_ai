"""Blind labelling sheet for the golden dataset (VCP_SPECIFICATION 57, 8.1; Phase 6 step 8).

Candidates are Trend Template passers (with weekly Stage 2) on chosen scan dates. The detector is
run on each candidate *only to balance the sample*: rare classes (A+) would otherwise hardly
appear. Its answer goes to a separate key file and is never shown on the sheet (section 57:
label from charts without detector output). By default the sheet also hides the symbol and
date, so hindsight about what the stock did later cannot colour the label; a checkbox reveals
them.

Outputs (one folder): ``candidates.json`` (everything needed to build fixtures, no detector
output), ``key.json`` (hidden: stratum and detector answer per candidate; open only after
labelling), ``labelling_sheet.html`` (charts, a label dropdown and notes per window, progress kept
in the browser, "Download CSV"). ``vcp research import-labels`` turns the CSV into fixtures.

Sampling: strata = A_PLUS_VCP, NEAR_A_PLUS (a VCP missing exactly one A+ rule: on 2024-10 ..
2026-09 the detector found no A+ at all, 518 of 539 VCPs failing the tight-pivot rule, so this
stratum is where candidate A+ charts are), VCP, VCP_LIKE, NONE (base but no class), NO_PATTERN,
and FAILED (status FAILED or INVALIDATED, whatever the class); up to ``per_stratum`` each, at
most one window per instrument per stratum and two overall, windows of one instrument at least
60 bars apart; deterministic for a seed; the sheet order is shuffled.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from vcp_scanner.config.models import ClassificationConfig, VCPThresholdsConfig
from vcp_scanner.data.repositories.duckdb_vcp_repository import DuckDBVCPRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import VCPClassification, VCPStatus
from vcp_scanner.patterns.vcp.detector import VCPDetector
from vcp_scanner.patterns.vcp.measurements import PriceSeries
from vcp_scanner.research.golden import fixture_id, series_to_json

CHART_BARS = 260
STRATA = ("A_PLUS_VCP", "NEAR_A_PLUS", "VCP", "VCP_LIKE", "NONE", "NO_PATTERN", "FAILED")
MIN_GAP_BARS = 60


@dataclass(frozen=True, slots=True)
class Candidate:
    id: str
    instrument_id: str
    symbol: str
    as_of: date
    trend_template_pass: bool
    weekly_stage2_pass: bool | None
    series: PriceSeries
    stratum: str  # hidden
    detector: dict[str, Any]  # hidden


def _stratum(det: Any) -> str:
    p = det.pattern
    if p is None:
        return "NO_PATTERN"
    if p.status in (VCPStatus.FAILED, VCPStatus.INVALIDATED):
        return "FAILED"
    if (
        p.classification is VCPClassification.VCP
        and det.classification is not None
        and len(det.classification.unmet[VCPClassification.A_PLUS_VCP]) == 1
    ):
        return "NEAR_A_PLUS"
    return str(p.classification.value)


def collect_candidates(
    store: DuckDBStore,
    dates: Sequence[date],
    config_hash: str,
    vcp: VCPThresholdsConfig,
    classification: ClassificationConfig,
) -> list[Candidate]:
    """Every Trend Template passer with weekly Stage 2 on ``dates`` (scans of ``config_hash``),
    with the hidden detector answer. Dates without such a scan are skipped."""
    repo = DuckDBVCPRepository(store)
    det = VCPDetector(vcp, classification, config_hash=config_hash)
    bars = max(CHART_BARS, vcp.lookback_bars())
    out: list[Candidate] = []
    for d in sorted(dates):
        rows = store.conn.execute(
            "SELECT t.instrument_id, i.symbol, t.weekly_stage2_pass FROM trend_template_results t"
            " JOIN instruments i USING (instrument_id) WHERE t.scan_id = ? AND t.status = 'PASS'"
            " AND t.weekly_stage2_pass ORDER BY t.instrument_id",
            [f"trend-{d.isoformat()}-{config_hash[:12]}"],
        ).fetchall()
        if not rows:
            continue
        series = repo.load_series([r[0] for r in rows], d, bars)
        for iid, symbol, stage2 in rows:
            s = series.get(iid)
            if s is None or not s.dates or s.dates[-1] != d:
                continue  # no bar on the date
            r = det.detect(iid, s, d, trend_template_pass=True, weekly_stage2_pass=stage2)
            p = r.pattern
            answer = {
                "classification": p.classification.value if p else None,
                "status": p.status.value if p else None,
                "no_pattern_reason": r.no_pattern_reason,
            }
            # The full series is kept: fixtures must be able to rerun the detector.
            out.append(Candidate(fixture_id(iid, d), iid, str(symbol), d, True, stage2,
                                 s, _stratum(r), answer))  # fmt: skip
    return out


def sample(candidates: Sequence[Candidate], per_stratum: int, seed: int) -> list[Candidate]:
    """Balanced, diverse, deterministic sample (module docstring), shuffled."""
    rng = random.Random(seed)
    by_stratum: dict[str, list[Candidate]] = {s: [] for s in STRATA}
    for c in sorted(candidates, key=lambda c: c.id):
        by_stratum[c.stratum].append(c)
    chosen: list[Candidate] = []
    used: dict[str, list[date]] = {}
    for stratum in STRATA:
        pool = by_stratum[stratum]
        rng.shuffle(pool)
        taken: set[str] = set()
        for c in pool:
            if len([x for x in chosen if x.stratum == stratum]) >= per_stratum:
                break
            dates = used.get(c.instrument_id, [])
            if c.instrument_id in taken or len(dates) >= 2:
                continue
            if any(abs((c.as_of - d).days) < MIN_GAP_BARS * 7 // 5 for d in dates):
                continue
            chosen.append(c)
            taken.add(c.instrument_id)
            used.setdefault(c.instrument_id, []).append(c.as_of)
    rng.shuffle(chosen)
    return chosen


# -- files ------------------------------------------------------------------------------------


def write_outputs(chosen: Sequence[Candidate], out_dir: Path, *, title: str) -> dict[str, Path]:
    """Write candidates.json, key.json and labelling_sheet.html into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, Any]] = [
        {
            "id": c.id,
            "number": k + 1,
            "instrument_id": c.instrument_id,
            "symbol": c.symbol,
            "as_of_date": c.as_of.isoformat(),
            "gates": {
                "trend_template_pass": c.trend_template_pass,
                "weekly_stage2_pass": c.weekly_stage2_pass,
            },  # fmt: skip
            "bars": series_to_json(c.series),
        }
        for k, c in enumerate(chosen)
    ]
    paths = {
        "candidates": out_dir / "candidates.json",
        "key": out_dir / "key.json",
        "sheet": out_dir / "labelling_sheet.html",
    }
    paths["candidates"].write_text(json.dumps(candidates, separators=(",", ":")))
    paths["key"].write_text(json.dumps(
        {"note": "Hidden detector answers. Do not open until labelling is finished.",
         "windows": {c.id: {"stratum": c.stratum, **c.detector} for c in chosen}},
        indent=1, sort_keys=True))  # fmt: skip
    charts = [
        {
            "id": c["id"],
            "n": c["number"],
            "sym": c["symbol"],
            "asof": c["as_of_date"],
            "d": c["bars"]["dates"][-CHART_BARS:],
            "h": [round(x, 2) for x in c["bars"]["high"][-CHART_BARS:]],
            "l": [round(x, 2) for x in c["bars"]["low"][-CHART_BARS:]],
            "c": [round(x, 2) for x in c["bars"]["close"][-CHART_BARS:]],
            "v": [None if x is None else round(x) for x in c["bars"]["volume"][-CHART_BARS:]],
        }
        for c in candidates
    ]
    html = (
        _SHEET.replace("__TITLE__", title)
        .replace("__HELP__", _LABEL_HELP)
        .replace("__OPTIONS__", _LABEL_OPTIONS)
        .replace("__DATA__", json.dumps(charts, separators=(",", ":")))
    )
    paths["sheet"].write_text(html)
    return paths


def import_labels(candidates_path: Path, labels_csv: Path, fixtures_root: Path) -> list[Path]:
    """Turn the sheet's CSV (id, label, notes) into fixtures under ``fixtures_root``.

    Rows without a label are skipped; an unknown label or id is an error (nothing is written).
    """
    import csv

    from vcp_scanner.research.golden import (
        LABELS,
        GoldenFixture,
        series_from_json,
        split_for,
        write_fixture,
    )

    by_id = {c["id"]: c for c in json.loads(candidates_path.read_text())}
    fixtures = []
    with labels_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = (row.get("label") or "").strip()
            if not label:
                continue
            if label not in LABELS:
                raise ValueError(f"unknown label {label!r} for {row.get('id')}")
            c = by_id.get(row["id"])
            if c is None:
                raise ValueError(f"unknown window id {row['id']!r}")
            fixtures.append(GoldenFixture(
                id=c["id"], instrument_id=c["instrument_id"], symbol=c["symbol"],
                as_of=date.fromisoformat(c["as_of_date"]), label=label,
                review_notes=(row.get("notes") or "").strip(),
                trend_template_pass=bool(c["gates"]["trend_template_pass"]),
                weekly_stage2_pass=c["gates"]["weekly_stage2_pass"],
                series=series_from_json(c["bars"]), split=split_for(c["id"]),
            ))  # fmt: skip
    return [write_fixture(fx, fixtures_root) for fx in fixtures]


from vcp_scanner.research._sheet_template import LABEL_HELP as _LABEL_HELP  # noqa: E402
from vcp_scanner.research._sheet_template import LABEL_OPTIONS as _LABEL_OPTIONS  # noqa: E402
from vcp_scanner.research._sheet_template import SHEET as _SHEET  # noqa: E402
