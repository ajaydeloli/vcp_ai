"""Multi-Strategy step 2: registry, strategy files and hashes, scan ids, the scoring split
(STRATEGY_SPECIFICATION 3-4, 9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vcp_scanner.config import load_scanner_config
from vcp_scanner.config.loader import scan_config_hash
from vcp_scanner.config.models import ScannerConfig, StrategyFileConfig, TrendGateConfig
from vcp_scanner.config.strategies import load_strategy_files, strategy_config_hash
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.domain.strategy import (
    VCP_GRADES,
    detector_scan_id,
    is_valid_strategy_id,
    score_scan_id,
)
from vcp_scanner.patterns.registry import REGISTRY, get_strategy, load_strategies
from vcp_scanner.scoring.components import ComponentScore
from vcp_scanner.scoring.engine import RANKED_CLASSES, VCP_PATTERN_SCORING, is_eligible
from vcp_scanner.scoring.final import final_score
from vcp_scanner.versioning import VCP_ALGORITHM_VERSION

ROOT = Path(__file__).resolve().parents[2]
REGISTERED = {"vcp": VCP_ALGORITHM_VERSION, "flat_base": "flat_base-1.0.0",
              "high_tight_flag": "high_tight_flag-1.0.0"}  # fmt: skip


def _write(folder: Path, name: str, text: str) -> None:
    (folder / "strategies").mkdir(parents=True, exist_ok=True)
    (folder / "strategies" / f"{name}.yaml").write_text(text, encoding="utf-8")


# -- ids and scan ids --------------------------------------------------------------------------


def test_strategy_ids_start_with_a_letter() -> None:
    assert is_valid_strategy_id("vcp") and is_valid_strategy_id("three_weeks_tight")
    assert not is_valid_strategy_id("3wt") and not is_valid_strategy_id("Flat")


def test_vcp_scan_ids_are_unchanged_and_others_carry_the_strategy() -> None:
    h = "64da9482a76952d8"
    assert detector_scan_id("vcp", "2026-10-01", h, "LIVE") == "vcp-2026-10-01-64da9482a769"
    assert score_scan_id("vcp", "2026-10-01", h, "LIVE") == "score-2026-10-01-64da9482a769"
    assert score_scan_id("vcp", "2026-10-01", h, "snap-1") == "score-2026-10-01-64da9482a769-snap-1"
    assert detector_scan_id("flat_base", "2026-10-01", h, "LIVE") == (
        "setup-flat_base-2026-10-01-64da9482a769"
    )
    assert score_scan_id("flat_base", "2026-10-01", h, "LIVE") == (
        "score-flat_base-2026-10-01-64da9482a769"
    )


# -- registry ----------------------------------------------------------------------------------


def test_registry_has_vcp_with_the_code_version_and_grades() -> None:
    vcp = get_strategy("vcp")
    assert set(REGISTRY) == {"vcp"}
    assert vcp.algorithm_version == VCP_ALGORITHM_VERSION
    assert vcp.tiers == ("NONE", "VCP_LIKE", "VCP", "A_PLUS_VCP")
    assert [vcp.grade(t) for t in vcp.tiers] == [0, 1, 2, 3]
    assert vcp.grade(None) == 0 and vcp.min_grade == 1
    assert vcp.scoring is VCP_PATTERN_SCORING


def test_unknown_strategy_is_refused() -> None:
    with pytest.raises(ConfigError, match="Unknown strategy"):
        get_strategy("cup_handle")


def test_ranked_classes_are_unchanged() -> None:
    assert frozenset({"VCP_LIKE", "VCP", "A_PLUS_VCP"}) == RANKED_CLASSES
    assert VCP_GRADES["NONE"] == 0


# -- strategy files ----------------------------------------------------------------------------


def test_repo_config_has_the_vcp_pointer_file() -> None:
    files = load_strategies(ROOT / "config")
    vcp = files["vcp"]
    assert vcp.config_source == "legacy" and vcp.stage == "paper" and vcp.enabled
    assert vcp.algorithm_version == VCP_ALGORITHM_VERSION
    assert vcp.trend_gate == TrendGateConfig()


def test_vcp_defaults_when_its_file_is_missing(tmp_path: Path) -> None:
    files = load_strategy_files(tmp_path, REGISTERED)
    assert set(files) == {"vcp"} and files["vcp"].config_source == "legacy"


@pytest.mark.parametrize(
    ("name", "text", "error"),
    [
        ("vcp", "strategy_id: vcp\nalgorithm_version: vcp-0.9.0\nconfig_source: legacy\n",
         "code implements"),
        ("cup_handle", "strategy_id: cup_handle\nalgorithm_version: x-1\n", "unknown strategy"),
        ("flat_base", "strategy_id: vcp\nalgorithm_version: vcp-1.1.0\n", "file name"),
        ("flat_base", "strategy_id: flat_base\nalgorithm_version: flat_base-1.0.0\n"
         "config_source: legacy\n", "vcp only"),
        ("vcp", f"strategy_id: vcp\nalgorithm_version: {VCP_ALGORITHM_VERSION}\n"
         "config_source: legacy\ndetector: {a: 1}\n", "legacy"),
        ("flat_base", "strategy_id: flat_base\nalgorithm_version: flat_base-1.0.0\n"
         "trend_gate: {mode: relaxed, waive: [above_52w_low]}\n", "may relax"),
        ("high_tight_flag", "strategy_id: high_tight_flag\n"
         "algorithm_version: high_tight_flag-1.0.0\n"
         "trend_gate: {mode: relaxed, waive: [near_52w_high]}\n", "may waive only"),
        ("flat_base", "strategy_id: flat_base\nalgorithm_version: flat_base-1.0.0\n"
         "trend_gate: {mode: required, waive: [above_52w_low]}\n", "only allowed"),
        ("flat_base", "strategy_id: flat_base\nalgorithm_version: flat_base-1.0.0\nbogus: 1\n",
         "Invalid strategy file"),
    ],
)  # fmt: skip
def test_bad_strategy_files_are_refused(tmp_path: Path, name: str, text: str, error: str) -> None:
    _write(tmp_path, name, text)
    with pytest.raises(ConfigError, match=error):
        load_strategy_files(tmp_path, REGISTERED)


def test_high_tight_flag_may_waive_the_52_week_low_rule(tmp_path: Path) -> None:
    _write(tmp_path, "high_tight_flag", "strategy_id: high_tight_flag\n"
           "algorithm_version: high_tight_flag-1.0.0\n"
           "trend_gate: {mode: relaxed, waive: [above_52w_low]}\n")  # fmt: skip
    files = load_strategy_files(tmp_path, REGISTERED)
    assert files["high_tight_flag"].trend_gate.waive == ("above_52w_low",)


# -- strategy config hashes (decision O1) ------------------------------------------------------


def test_vcp_hash_is_the_scan_config_hash_and_pinned() -> None:
    cfg = load_scanner_config(ROOT / "config")
    vcp = load_strategies(ROOT / "config")["vcp"]
    assert strategy_config_hash(cfg, vcp) == scan_config_hash(cfg)
    # The research history and the live scans are stored under this hash.
    assert scan_config_hash(cfg).startswith("64da9482a769")


def _flat(**kw: object) -> StrategyFileConfig:
    base: dict[str, object] = {"strategy_id": "flat_base", "algorithm_version": "flat_base-1.0.0",
                               "detector": {"max_depth_pct": 15.0}}  # fmt: skip
    return StrategyFileConfig(**{**base, **kw})  # type: ignore[arg-type]


def test_other_strategies_chain_on_the_scan_hash() -> None:
    cfg = ScannerConfig()
    h = strategy_config_hash(cfg, _flat())
    assert h != scan_config_hash(cfg) and len(h) == 64
    # Their own thresholds and version fork it; enabled / stage do not.
    assert strategy_config_hash(cfg, _flat(detector={"max_depth_pct": 12.0})) != h
    assert strategy_config_hash(cfg, _flat(algorithm_version="flat_base-1.0.1")) != h
    assert strategy_config_hash(cfg, _flat(enabled=False, stage="paper")) == h
    # A shared (scan) config change forks it too.
    other = ScannerConfig.model_validate({"universe": {"min_close_price": 50.0}})
    assert strategy_config_hash(other, _flat()) != h


def test_adding_a_strategy_file_does_not_change_the_scan_hash(tmp_path: Path) -> None:
    before = scan_config_hash(load_scanner_config(ROOT / "config"))
    for f in (ROOT / "config").glob("*.yaml"):
        (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    _write(tmp_path, "flat_base", "strategy_id: flat_base\nalgorithm_version: flat_base-1.0.0\n")
    assert scan_config_hash(load_scanner_config(tmp_path)) == before


# -- scoring split -----------------------------------------------------------------------------


def _comp(name: str, score: float | None) -> ComponentScore:
    return ComponentScore(name, score, ())


def test_pattern_component_takes_the_pattern_weight_under_any_name() -> None:
    w = ScannerConfig().strategy.scoring.weights
    as_vcp = final_score([_comp("TREND", 60), _comp("VCP", 80), _comp("VOLUME", 40),
                          _comp("RS", 90)], w)  # fmt: skip
    as_pattern = final_score([_comp("TREND", 60), _comp("PATTERN", 80), _comp("VOLUME", 40),
                              _comp("RS", 90)], w, "PATTERN")  # fmt: skip
    assert as_vcp.final == as_pattern.final
    assert as_vcp.effective_weights["VCP"] == as_pattern.effective_weights["PATTERN"]
    missing = final_score([_comp("TREND", 60), _comp("VOLUME", 40), _comp("RS", 90)], w,
                          "PATTERN")  # fmt: skip
    assert "PATTERN_UNAVAILABLE" in missing.flags and "VCP_UNAVAILABLE" not in missing.flags


def test_eligibility_by_grade_matches_the_vcp_rule() -> None:
    from vcp_scanner.scoring.engine import PatternInputs

    def p(cls: str, status: str) -> PatternInputs:
        return PatternInputs(cls, status, "CONFIRMED", 3, 0.5, 5.0, 0.6, 3.0, 20.0, 0.5)

    for cls in ("NONE", "VCP_LIKE", "VCP", "A_PLUS_VCP"):
        for status in ("FORMING", "PIVOT_READY", "BREAKOUT", "FAILED", "INVALIDATED",
                       "STALE_DATA"):  # fmt: skip
            old = cls in {"VCP_LIKE", "VCP", "A_PLUS_VCP"} and status in {
                "FORMING", "PIVOT_READY", "BREAKOUT"}  # fmt: skip
            assert is_eligible(p(cls, status)) == old == VCP_PATTERN_SCORING.eligible(
                p(cls, status))  # fmt: skip
    assert not is_eligible(None)


# -- CLI ---------------------------------------------------------------------------------------


def test_config_hash_per_strategy(capsys: pytest.CaptureFixture[str]) -> None:
    from vcp_scanner.cli import main

    cfg_dir = str(ROOT / "config")
    assert main(["config", "hash", "--config-dir", cfg_dir, "--strategy", "vcp"]) == 0
    assert capsys.readouterr().out.strip() == scan_config_hash(load_scanner_config(cfg_dir))
    assert main(["config", "hash", "--config-dir", cfg_dir, "--strategy", "cup_handle"]) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["compute", "setups", "--as-of", "2026-10-01", "--strategy", "cup_handle"],
        ["compute", "scores", "--as-of", "2026-10-01", "--strategy", "cup_handle"],
        ["compute", "labels", "--strategy", "cup_handle"],
        ["backtest", "run", "--from", "2026-01-01", "--to", "2026-02-01", "--strategy", "x"],
        ["backtest", "run", "--from", "2026-01-01", "--to", "2026-02-01", "--classes", "FLAT"],
    ],
)
def test_unknown_strategies_and_tiers_are_refused(
    tmp_path: Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    from vcp_scanner.cli import main

    db = str(tmp_path / "x.duckdb")
    assert main([*argv, "--db", db, "--config-dir", str(ROOT / "config")]) == 1
    err = capsys.readouterr().err
    assert "Unknown strategy" in err or "Unknown classes" in err
