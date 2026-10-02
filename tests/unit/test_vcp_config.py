"""Phase 6 configuration additions: prior advance, base, invalidation, VCP lookback
(VCP_SPECIFICATION 7, 8.1, 25, 60)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from vcp_scanner.config import (
    DataConfig,
    ScannerConfig,
    StrategyConfig,
    VCPThresholdsConfig,
    load_scanner_config,
)
from vcp_scanner.config.models import QualityGateConfig


def _scanner(vcp: dict[str, Any], lifetime: int | None = 253) -> ScannerConfig:
    return ScannerConfig(
        strategy=StrategyConfig(vcp=VCPThresholdsConfig(**vcp)),
        data=DataConfig(quality=QualityGateConfig(block_lifetime_bars=lifetime)),
    )


def test_defaults_match_owner_decisions() -> None:
    vcp = load_scanner_config("config").strategy.vcp
    assert vcp.prior_advance.enabled and vcp.prior_advance.lookback_days == 120
    assert vcp.prior_advance.min_return_pct == 20.0
    assert vcp.base.max_duration_days == 130
    assert vcp.invalidation.trend_template_failure
    assert vcp.invalidation.base_low_break_pct == 2.0
    assert vcp.invalidation.volatility_expansion_multiple == 2.0


def test_vcp_lookback_default_fits_block_lifetime() -> None:
    # 130 base bars + 119 prior-advance bars before the base-start high = 249 <= 253.
    assert VCPThresholdsConfig().lookback_bars() == 249
    cfg = ScannerConfig()
    assert cfg.longest_lookback_bars() == 253 == cfg.data.quality.block_lifetime_bars


def test_longer_base_is_refused_by_the_block_lifetime() -> None:
    assert _scanner({"base": {"max_duration_days": 134}})  # 134 + 119 = 253
    with pytest.raises(ValidationError, match="longest lookback"):
        _scanner({"base": {"max_duration_days": 135}})  # 254
    with pytest.raises(ValidationError, match="longest lookback"):
        _scanner({"prior_advance": {"lookback_days": 125}})  # 130 + 124 = 254
    # A longer lifetime admits the longer base (section 8.1 item 4).
    assert _scanner({"base": {"max_duration_days": 200}}, lifetime=400)
    # No lifetime (blocks forever) never conflicts.
    assert _scanner({"base": {"max_duration_days": 200}}, lifetime=None)


def test_disabled_prior_advance_drops_out_of_the_lookback() -> None:
    vcp = VCPThresholdsConfig(prior_advance={"enabled": False})  # type: ignore[arg-type]
    assert vcp.lookback_bars() == 130 + 50  # long volume average before the base start


def test_lookback_counts_the_longer_pre_base_input() -> None:
    vcp = VCPThresholdsConfig(  # type: ignore[call-arg]
        prior_advance={"lookback_days": 40}, volume={"long_period": 60}
    )
    assert vcp.lookback_bars() == 130 + 60


def test_base_must_hold_minimum_contractions() -> None:
    with pytest.raises(ValidationError, match="cannot hold"):
        VCPThresholdsConfig(base={"max_duration_days": 5})  # type: ignore[arg-type]
    assert VCPThresholdsConfig(base={"max_duration_days": 6})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad",
    [
        {"prior_advance": {"lookback_days": 1}},
        {"prior_advance": {"min_return_pct": 0}},
        {"prior_advance": {"unknown": 1}},
        {"base": {"max_duration_days": 1}},
        {"invalidation": {"base_low_break_pct": -1}},
        {"invalidation": {"volatility_expansion_multiple": 1.0}},
    ],
)
def test_invalid_new_blocks_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        VCPThresholdsConfig(**bad)


def test_new_keys_change_the_strategy_hash() -> None:
    from vcp_scanner.config.loader import scan_config_hash

    base = ScannerConfig()
    other = _scanner({"base": {"max_duration_days": 120}})
    assert scan_config_hash(base) != scan_config_hash(other)
