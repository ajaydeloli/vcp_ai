"""Walk-forward periods (backtest/periods.py; Phase 9 step 4)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from vcp_scanner.backtest.periods import BacktestConfig, load_backtest_config, months_between

REPO = Path(__file__).resolve().parents[2]


def test_shipped_config_matches_the_owner_decision() -> None:
    cfg = load_backtest_config(REPO / "config")
    assert cfg == BacktestConfig()  # yaml and defaults agree
    by = {p.name: p for p in cfg.periods}
    assert by["development"].start == date(2022, 2, 1) and by["development"].end == date(
        2024, 6, 30
    )
    assert by["validation"].role == "validation" and by["validation"].end == date(2026, 9, 30)
    assert by["live_paper"].role == "test" and by["live_paper"].end is None
    assert load_backtest_config(REPO / "no-such-dir") == BacktestConfig()


def test_overlap_order_and_names_are_checked() -> None:
    dev = {"name": "d", "role": "development", "start": "2022-01-01", "end": "2023-01-01"}
    val = {"name": "v", "role": "validation", "start": "2022-06-01", "end": "2024-01-01"}
    with pytest.raises(ValidationError, match="overlap"):
        BacktestConfig(periods=[dev, val])  # type: ignore[list-item]
    late_dev = {"name": "d2", "role": "development", "start": "2025-01-01", "end": "2025-06-01"}
    ok_val = {**val, "start": "2023-02-01"}
    with pytest.raises(ValidationError, match="development, then validation"):
        BacktestConfig(periods=[dev, ok_val, late_dev])  # type: ignore[list-item]
    with pytest.raises(ValidationError, match="unique"):
        BacktestConfig(periods=[dev, {**ok_val, "name": "d"}])  # type: ignore[list-item]


def test_months_between() -> None:
    assert months_between(date(2026, 10, 1), date(2027, 4, 1)) == 6
    assert months_between(date(2026, 10, 1), date(2026, 10, 31)) == 0


def test_backtest_defaults_are_the_step_7d_choice() -> None:
    from vcp_scanner.backtest.periods import BacktestDefaults

    d = load_backtest_config(REPO / "config").defaults
    assert (d.entry, d.regime, d.rule) == ("cross_5", "breadth50", "hold_low8")
    with pytest.raises(ValueError, match="defaults.entry"):
        BacktestDefaults(entry="gap")
    with pytest.raises(ValueError, match="defaults.regime"):
        BacktestDefaults(regime="nifty")
    with pytest.raises(ValueError, match="defaults.rule"):
        BacktestDefaults(rule="t99")
