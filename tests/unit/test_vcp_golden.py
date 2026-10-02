"""Golden-dataset harness and blind labelling sheet (VCP_SPECIFICATION 57; Phase 6 step 8)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from vcp_scanner.config.models import ClassificationConfig, VCPThresholdsConfig
from vcp_scanner.patterns.vcp.measurements import PriceSeries
from vcp_scanner.research.golden import (
    GoldenFixture,
    agrees,
    detector_answer,
    evaluate,
    fixture_from_json,
    fixture_id,
    fixture_to_json,
    format_report,
    load_fixtures,
    run_fixture,
    split_for,
    write_fixture,
)
from vcp_scanner.research.labelling import Candidate, import_labels, sample, write_outputs

D0 = date(2026, 1, 1)
CLASSIC = [(60, 100), (20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (5, 135), (5, 140)]
VCP_CFG = VCPThresholdsConfig(
    swing={"left_bars": 2, "right_bars": 2},  # type: ignore[arg-type]
    base={"max_duration_days": 80},  # type: ignore[arg-type]
    prior_advance={"lookback_days": 20},  # type: ignore[arg-type]
)
CLS = ClassificationConfig()


def _series(legs: list[tuple[int, float]], dryup: bool = True) -> PriceSeries:
    closes = [100.0]
    for bars, target in legs:
        a = closes[-1]
        closes += [a + (target - a) * (i + 1) / bars for i in range(bars)]
    vol = [1000.0] * len(closes)
    if dryup:
        for i in range(81, min(91, len(vol))):
            vol[i] = 1500.0
        for i in range(115, min(120, len(vol))):
            vol[i] = 400.0
    d = [D0 + timedelta(days=i) for i in range(len(closes))]
    return PriceSeries(d, [c * 1.002 for c in closes], [c * 0.998 for c in closes], closes, vol,
                       [2.0] * len(closes))  # fmt: skip


def _fx(label: str, s: PriceSeries, iid: str = "I") -> GoldenFixture:
    fid = fixture_id(iid, s.dates[-1])
    return GoldenFixture(fid, iid, iid, s.dates[-1], label, "", True, True, s, split_for(fid))


def test_split_is_fixed_and_about_thirty_percent() -> None:
    ids = [f"g-{i}" for i in range(2000)]
    share = sum(split_for(i) == "holdout" for i in ids) / len(ids)
    assert 0.27 < share < 0.33
    assert [split_for(i) for i in ids[:50]] == [split_for(i) for i in ids[:50]]


def test_json_round_trip_and_files(tmp_path: Path) -> None:
    fx = _fx("confirmed_a_plus", _series(CLASSIC))
    again = fixture_from_json(json.loads(json.dumps(fixture_to_json(fx))))
    assert again == fx
    path = write_fixture(fx, tmp_path)
    assert path == tmp_path / "confirmed_a_plus" / f"{fx.id}.json"
    assert load_fixtures(tmp_path) == [fx]
    path.rename(tmp_path / "confirmed_a_plus" / "x.json")
    (tmp_path / "vcp_like").mkdir()
    (tmp_path / "confirmed_a_plus" / "x.json").rename(tmp_path / "vcp_like" / "x.json")
    with pytest.raises(ValueError, match="but folder"):
        load_fixtures(tmp_path)
    with pytest.raises(ValueError, match="unknown label"):
        replace(fx, label="great")


def test_agreement_and_report() -> None:
    a_plus = _fx("confirmed_a_plus", _series(CLASSIC), "A")
    flat = _fx("non_vcp", _series([(60, 100), (20, 110), (10, 100), (10, 108)], dryup=False), "B")
    unsure = _fx("ambiguous", _series(CLASSIC), "C")
    wrong = _fx("vcp_like", _series(CLASSIC), "D")  # detector says A+: disagreement
    assert agrees(a_plus, run_fixture(a_plus, VCP_CFG, CLS)) is True
    assert agrees(flat, run_fixture(flat, VCP_CFG, CLS)) is True
    assert agrees(unsure, run_fixture(unsure, VCP_CFG, CLS)) is None
    r = evaluate([a_plus, flat, unsure, wrong], VCP_CFG, CLS)
    assert r.n == 4 and r.excluded_ambiguous == 1
    assert r.agreement == pytest.approx(2 / 3)
    assert r.production == (pytest.approx(0.5), 1.0)  # 2 A+ predicted, 1 labelled A+
    assert r.confusion[("non_vcp", "NO_PATTERN")] == 1
    assert r.per_class["A_PLUS_VCP"] == (pytest.approx(0.5), 1.0, 1)
    assert "agreement  66.7%" in format_report(r)
    only_dev = evaluate([a_plus, flat], VCP_CFG, CLS, split="no-such-split")
    assert only_dev.n == 0 and only_dev.agreement is None


def test_failed_label_is_judged_on_status() -> None:
    legs = [*CLASSIC, (1, 143.0), (1, 139.0)]
    s = _series(legs)
    s = replace(s, volume=[*s.volume[:-2], 2000.0, 1000.0])  # breakout on volume, then back
    fx = _fx("failed_vcp", s)
    d = run_fixture(fx, VCP_CFG, CLS)
    assert d.pattern is not None and d.pattern.status.value == "FAILED"
    assert agrees(fx, d) is True
    assert detector_answer(d, "vcp-1.0.0")["status"] == "FAILED"


def _cand(iid: str, d: date, stratum: str) -> Candidate:
    s = _series(CLASSIC)
    return Candidate(fixture_id(iid, d), iid, iid, d, True, True, s, stratum,
                     {"classification": stratum})  # fmt: skip


def test_sampling_is_balanced_diverse_and_deterministic() -> None:
    cands = [_cand(f"S{i}", D0 + timedelta(days=30 * k), st)
             for i in range(6) for k in range(4) for st in ("VCP", "NONE")]  # fmt: skip
    a = sample(cands, per_stratum=3, seed=1)
    assert a == sample(cands, per_stratum=3, seed=1)
    assert sum(c.stratum == "VCP" for c in a) == 3 and sum(c.stratum == "NONE" for c in a) == 3
    per_inst: dict[str, list[date]] = {}
    for c in a:
        per_inst.setdefault(c.instrument_id, []).append(c.as_of)
    assert all(len(v) <= 2 for v in per_inst.values())
    assert all(abs((x - y).days) >= 84 for v in per_inst.values() if len(v) == 2
               for x, y in [v])  # fmt: skip


def test_sheet_files_are_blind_and_import_makes_fixtures(tmp_path: Path) -> None:
    chosen = [_cand("S1", D0 + timedelta(days=200), "A_PLUS_VCP"),
              _cand("S2", D0 + timedelta(days=200), "NONE")]  # fmt: skip
    paths = write_outputs(chosen, tmp_path / "sheet", title="test sheet")
    cands = json.loads(paths["candidates"].read_text())
    assert [c["number"] for c in cands] == [1, 2]
    assert "stratum" not in paths["candidates"].read_text()
    assert "A_PLUS_VCP" not in paths["sheet"].read_text()  # no detector output on the sheet
    key = json.loads(paths["key"].read_text())["windows"]
    assert key[chosen[0].id]["stratum"] == "A_PLUS_VCP"
    csv = tmp_path / "labels.csv"
    csv.write_text(f"id,window,label,notes\n{chosen[0].id},1,confirmed_vcp,\"nice, tight\"\n"
                   f"{chosen[1].id},2,,\n")  # fmt: skip
    written = import_labels(paths["candidates"], csv, tmp_path / "fx")
    assert len(written) == 1 and written[0].parent.name == "confirmed_vcp"
    fx = load_fixtures(tmp_path / "fx")[0]
    assert fx.review_notes == "nice, tight" and fx.detector_baseline is None
    csv.write_text(f"id,window,label,notes\n{chosen[0].id},1,great,\n")
    with pytest.raises(ValueError, match="unknown label"):
        import_labels(paths["candidates"], csv, tmp_path / "fx2")
