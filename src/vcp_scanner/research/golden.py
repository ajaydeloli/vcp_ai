"""Golden dataset for the VCP detector (VCP_SPECIFICATION 57, 63; Phase 6 step 8).

A fixture is one JSON file, ``tests/fixtures/vcp/<label>/<id>.json``, holding everything needed to
rerun the detector offline: the human label and notes, the gate verdicts of that date, and the
bars up to the as-of date (adjusted high/low/close, volume, the feature engine's ATR%). The
regression suite (``tests/regression/test_vcp_golden.py``) and ``vcp research golden`` run the
detector on every fixture and report agreement; no database is involved, so results are exactly
reproducible.

Protocol (section 57): labels come from charts without detector output; ``ambiguous`` is excluded
from precision/recall; 30 % of fixtures (by a fixed hash of the id) are ``holdout`` and must never
be used for threshold tuning (``evaluate(split="development")`` is the tuning view).

``detector_baseline`` records the detector's answer when the fixture was last re-recorded; the
regression test fails when the answer changes, so every structural change is reviewed and
re-recorded on purpose (``vcp research golden --record``).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from vcp_scanner.config.models import ClassificationConfig, VCPThresholdsConfig
from vcp_scanner.domain.enums import VCPClassification, VCPStatus
from vcp_scanner.patterns.vcp.detector import VCPDetection, VCPDetector
from vcp_scanner.patterns.vcp.measurements import PriceSeries

FIXTURE_VERSION = 1
HOLDOUT_PCT = 30

#: Human labels (directory names, section 57) and the detector answer each one expects.
LABELS: dict[str, VCPClassification | None] = {
    "confirmed_a_plus": VCPClassification.A_PLUS_VCP,
    "confirmed_vcp": VCPClassification.VCP,
    "vcp_like": VCPClassification.VCP_LIKE,
    "non_vcp": VCPClassification.NONE,
    "failed_vcp": None,  # judged on status: FAILED or INVALIDATED
    "ambiguous": None,  # excluded from metrics
}
FAILED_STATUSES = (VCPStatus.FAILED, VCPStatus.INVALIDATED)


def split_for(fixture_id: str) -> str:
    """``holdout`` for a fixed 30 % of ids (by hash), else ``development``."""
    bucket = int(hashlib.sha256(fixture_id.encode()).hexdigest(), 16) % 100
    return "holdout" if bucket < HOLDOUT_PCT else "development"


def fixture_id(instrument_id: str, as_of: date) -> str:
    raw = f"{instrument_id}|{as_of.isoformat()}"
    return "g-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class GoldenFixture:
    id: str
    instrument_id: str
    symbol: str
    as_of: date
    label: str
    review_notes: str
    trend_template_pass: bool
    weekly_stage2_pass: bool | None
    series: PriceSeries
    split: str
    detector_baseline: dict[str, Any] | None = None
    path: Path | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.label not in LABELS:
            raise ValueError(f"unknown label {self.label!r} (one of {sorted(LABELS)})")


# -- JSON (de)serialisation -------------------------------------------------------------------


def series_to_json(s: PriceSeries) -> dict[str, list[Any]]:
    return {
        "dates": [d.isoformat() for d in s.dates],
        "high": list(s.high),
        "low": list(s.low),
        "close": list(s.close),
        "volume": list(s.volume),
        "atr_pct_14": list(s.atr_pct_14),
    }


def series_from_json(data: dict[str, list[Any]]) -> PriceSeries:
    return PriceSeries(
        [date.fromisoformat(d) for d in data["dates"]],
        [float(x) for x in data["high"]],
        [float(x) for x in data["low"]],
        [float(x) for x in data["close"]],
        [None if x is None else float(x) for x in data["volume"]],
        [None if x is None else float(x) for x in data["atr_pct_14"]],
    )


def fixture_to_json(fx: GoldenFixture) -> dict[str, Any]:
    return {
        "fixture_version": FIXTURE_VERSION,
        "id": fx.id,
        "instrument_id": fx.instrument_id,
        "symbol": fx.symbol,
        "as_of_date": fx.as_of.isoformat(),
        "label": fx.label,
        "review_notes": fx.review_notes,
        "split": fx.split,
        "gates": {
            "trend_template_pass": fx.trend_template_pass,
            "weekly_stage2_pass": fx.weekly_stage2_pass,
        },
        "detector_baseline": fx.detector_baseline,
        "bars": series_to_json(fx.series),
    }


def fixture_from_json(data: dict[str, Any], path: Path | None = None) -> GoldenFixture:
    if data.get("fixture_version") != FIXTURE_VERSION:
        raise ValueError(f"{path}: unsupported fixture_version {data.get('fixture_version')}")
    return GoldenFixture(
        id=data["id"],
        instrument_id=data["instrument_id"],
        symbol=data["symbol"],
        as_of=date.fromisoformat(data["as_of_date"]),
        label=data["label"],
        review_notes=data.get("review_notes", ""),
        trend_template_pass=bool(data["gates"]["trend_template_pass"]),
        weekly_stage2_pass=data["gates"]["weekly_stage2_pass"],
        series=series_from_json(data["bars"]),
        split=data.get("split") or split_for(data["id"]),
        detector_baseline=data.get("detector_baseline"),
        path=path,
    )


def write_fixture(fx: GoldenFixture, root: Path) -> Path:
    """Write ``root/<label>/<id>.json`` (stable key order, one line per bar list)."""
    out = root / fx.label / f"{fx.id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture_to_json(fx), indent=1, sort_keys=True) + "\n")
    return out


def load_fixtures(root: Path) -> list[GoldenFixture]:
    """Every ``*.json`` under ``root`` (any depth), sorted by id. The folder must match the
    label, so a file moved between class folders is caught."""
    out = []
    for path in sorted(root.rglob("*.json")):
        fx = fixture_from_json(json.loads(path.read_text()), path)
        if path.parent.name != fx.label:
            raise ValueError(f"{path}: label {fx.label!r} but folder {path.parent.name!r}")
        out.append(fx)
    return sorted(out, key=lambda f: f.id)


# -- running and scoring ----------------------------------------------------------------------


def run_fixture(
    fx: GoldenFixture, vcp: VCPThresholdsConfig, classification: ClassificationConfig
) -> VCPDetection:
    det = VCPDetector(vcp, classification, config_hash="golden")
    return det.detect(
        fx.instrument_id, fx.series, fx.as_of,
        trend_template_pass=fx.trend_template_pass, weekly_stage2_pass=fx.weekly_stage2_pass,
    )  # fmt: skip


def detector_answer(d: VCPDetection, algorithm_version: str) -> dict[str, Any]:
    """The part of a detection that the baseline records (and the regression test compares)."""
    p = d.pattern
    return {
        "algorithm_version": algorithm_version,
        "classification": p.classification.value if p else None,
        "status": p.status.value if p else (d.status.value if d.status else None),
        "contraction_count": p.contraction_count if p else None,
        "no_pattern_reason": d.no_pattern_reason,
    }


def agrees(fx: GoldenFixture, d: VCPDetection) -> bool | None:
    """Does the detector agree with the human label? ``None`` for ``ambiguous``."""
    if fx.label == "ambiguous":
        return None
    p = d.pattern
    if fx.label == "failed_vcp":
        return p is not None and p.status in FAILED_STATUSES
    expected = LABELS[fx.label]
    got = p.classification if p else VCPClassification.NONE
    return got is expected


@dataclass(frozen=True, slots=True)
class GoldenReport:
    n: int
    excluded_ambiguous: int
    agreement: float | None
    #: (human label, detector class or NO_PATTERN) -> count
    confusion: dict[tuple[str, str], int]
    #: detector class -> (precision, recall, support) against the matching label
    per_class: dict[str, tuple[float | None, float | None, int]]
    #: production (VCP or A+) precision/recall against confirmed_vcp + confirmed_a_plus
    production: tuple[float | None, float | None]


def _ratio(a: int, b: int) -> float | None:
    return a / b if b else None


def evaluate(
    fixtures: Iterable[GoldenFixture],
    vcp: VCPThresholdsConfig,
    classification: ClassificationConfig,
    split: str | None = None,
) -> GoldenReport:
    """Score the detector on ``fixtures`` (optionally one split), section 63 detector metrics."""
    chosen = [f for f in fixtures if split is None or f.split == split]
    confusion: Counter[tuple[str, str]] = Counter()
    agree = scored = ambiguous = 0
    pairs: list[tuple[str, str]] = []
    for fx in chosen:
        d = run_fixture(fx, vcp, classification)
        got = d.pattern.classification.value if d.pattern else "NO_PATTERN"
        confusion[(fx.label, got)] += 1
        ok = agrees(fx, d)
        if ok is None:
            ambiguous += 1
            continue
        scored += 1
        agree += ok
        pairs.append((fx.label, got))

    per_class: dict[str, tuple[float | None, float | None, int]] = {}
    for label, cls in LABELS.items():
        if cls is None:
            continue
        name = cls.value
        predicted = sum(1 for _, g in pairs if g == name or (name == "NONE" and g == "NO_PATTERN"))
        actual = sum(1 for lab, _ in pairs if lab == label)
        hit = sum(1 for lab, g in pairs if lab == label
                  and (g == name or (name == "NONE" and g == "NO_PATTERN")))  # fmt: skip
        per_class[name] = (_ratio(hit, predicted), _ratio(hit, actual), actual)
    prod = {"VCP", "A_PLUS_VCP"}
    good = {"confirmed_vcp", "confirmed_a_plus"}
    p_pred = sum(1 for _, g in pairs if g in prod)
    p_act = sum(1 for lab, _ in pairs if lab in good)
    p_hit = sum(1 for lab, g in pairs if lab in good and g in prod)
    return GoldenReport(
        n=len(chosen),
        excluded_ambiguous=ambiguous,
        agreement=_ratio(agree, scored),
        confusion=dict(confusion),
        per_class=per_class,
        production=(_ratio(p_hit, p_pred), _ratio(p_hit, p_act)),
    )


def format_report(r: GoldenReport, labels: Sequence[str] = tuple(LABELS)) -> str:
    def pct(x: float | None) -> str:
        return "  -  " if x is None else f"{x * 100:5.1f}%"

    lines = [
        f"fixtures {r.n} (ambiguous excluded: {r.excluded_ambiguous}); "
        f"agreement {pct(r.agreement)}",  # fmt: skip
        f"production (VCP/A+): precision {pct(r.production[0])} recall {pct(r.production[1])}",
    ]
    for name, (p, rc, n) in r.per_class.items():
        lines.append(f"  {name:11} precision {pct(p)} recall {pct(rc)} (labelled {n})")
    cols = ["A_PLUS_VCP", "VCP", "VCP_LIKE", "NONE", "NO_PATTERN"]
    lines.append("  confusion (label -> detector): " + " | ".join(cols))
    for lab in labels:
        row = [r.confusion.get((lab, c), 0) for c in cols]
        if any(row):
            lines.append(f"    {lab:17} " + " ".join(f"{v:5d}" for v in row))
    return "\n".join(lines)
