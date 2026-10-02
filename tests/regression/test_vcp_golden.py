"""Golden-dataset regression suite (VCP_SPECIFICATION 57, Phase 6 step 8).

Every structural detector change runs against the owner-labelled fixtures in
``tests/fixtures/vcp/``. Each fixture's detector answer must equal its recorded
``detector_baseline``: a change is reviewed with ``vcp research golden`` (precision/recall per
class, holdout kept apart) and re-recorded on purpose with ``--record``. Acceptance thresholds
are set by the owner once the first labelled set exists; until then the report is informational.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vcp_scanner.config import load_scanner_config
from vcp_scanner.research.golden import (
    detector_answer,
    evaluate,
    format_report,
    load_fixtures,
    run_fixture,
)
from vcp_scanner.versioning import VCP_ALGORITHM_VERSION

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vcp"
CFG = load_scanner_config(str(ROOT / "config")).strategy
_FIXTURES = load_fixtures(FIXTURES) if FIXTURES.exists() else []


@pytest.mark.skipif(not _FIXTURES, reason="no labelled golden fixtures yet")
@pytest.mark.parametrize("fx", _FIXTURES, ids=[f.id for f in _FIXTURES])
def test_detector_answer_matches_recorded_baseline(fx) -> None:  # type: ignore[no-untyped-def]
    if fx.detector_baseline is None:
        pytest.fail(f"{fx.path}: no detector baseline; run `vcp research golden --record`")
    got = detector_answer(run_fixture(fx, CFG.vcp, CFG.classification), VCP_ALGORITHM_VERSION)
    assert got == fx.detector_baseline, (
        f"{fx.symbol} {fx.as_of} ({fx.label}): detector answer changed; review with "
        "`vcp research golden` and re-record with --record if intended"
    )


@pytest.mark.skipif(not _FIXTURES, reason="no labelled golden fixtures yet")
def test_golden_report(capsys: pytest.CaptureFixture[str]) -> None:
    for split in ("development", "holdout"):
        report = evaluate(_FIXTURES, CFG.vcp, CFG.classification, split)
        with capsys.disabled():
            print(f"\n[golden {split}]\n" + format_report(report))
