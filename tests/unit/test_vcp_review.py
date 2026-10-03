"""Mark-check review sheet (research/review.py, Phase 6 validation option A)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from tests.unit.test_vcp_golden import CLASSIC, CLS, VCP_CFG, _series
from vcp_scanner.research.golden import fixture_id
from vcp_scanner.research.labelling import Candidate
from vcp_scanner.research.review import build_windows, marks, select, write_review


def _cand(iid: str, d: date, stratum: str) -> Candidate:
    s = _series(CLASSIC)
    return Candidate(fixture_id(iid, d), iid, iid, d, True, True, s, stratum, {"x": stratum})


def test_select_respects_quotas_and_is_deterministic() -> None:
    d0 = date(2025, 1, 1)
    cands = [_cand(f"I{k}", d0 + timedelta(days=k), st)
             for k, st in enumerate(["VCP"] * 9 + ["NONE"] * 9 + ["NO_PATTERN"] * 3)]  # fmt: skip
    quotas = {"VCP": 4, "NONE": 2, "NO_PATTERN": 0}
    a = select(cands, 7, quotas)
    assert [c.stratum for c in a].count("VCP") == 4
    assert [c.stratum for c in a].count("NONE") == 2
    assert "NO_PATTERN" not in {c.stratum for c in a}
    assert [c.id for c in a] == [c.id for c in select(cands, 7, quotas)]


def test_marks_follow_detector_pattern_and_files(tmp_path: Path) -> None:
    s = _series(CLASSIC)
    c = Candidate(fixture_id("A", s.dates[-1]), "A", "AAA", s.dates[-1], True, True, s,
                  "A_PLUS_VCP", {"classification": "A_PLUS_VCP"})  # fmt: skip
    (w,) = build_windows([c], VCP_CFG, CLS, "h" * 64)
    m = w["m"]
    assert len(m["t"]) >= 2 and m["bs"] >= 0 and m["pv"] is not None
    for t in m["t"]:  # every mark sits on a chart bar at the stated price
        assert 0 <= t["p"] < t["q"] < len(w["d"])
        assert abs(w["h"][t["p"]] - t["ph"]) < 0.02 and abs(w["l"][t["q"]] - t["ql"]) < 0.02
    assert m["txt"].startswith("A_PLUS_VCP")
    paths = write_review([w], tmp_path, title="t")
    meta = json.loads(paths["windows"].read_text())
    assert meta[0]["sym"] == "AAA" and "h" not in meta[0]
    html = paths["sheet"].read_text()
    assert "__" not in html.replace("__proto__", "") and "Yes, sensible" in html


def test_marks_off_chart_and_no_pattern() -> None:
    assert marks(None, ["2025-01-01"], "No pattern") == {"bs": -1, "t": [], "pv": None,
                                                        "txt": "No pattern"}  # fmt: skip
