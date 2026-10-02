"""Unit tests for configuration validation, loading, and hashing.

Covers hypotheses and bounds specified in:
- TREND_TEMPLATE_SPECIFICATION section 5
- SCORING_SPECIFICATION sections 8-9
- PROJECT_DESIGN sections 45-47, 60
"""

import pytest
from pydantic import ValidationError

from vcp_scanner.config import (
    ClassificationConfig,
    NormalizationBounds,
    RSConfig,
    ScannerConfig,
    ScoringConfig,
    StrategyConfig,
    TierClassificationConfig,
    TrendTemplateConfig,
    VCPThresholdsConfig,
    compute_config_hash,
    load_scanner_config,
)
from vcp_scanner.domain.errors import ConfigError


def test_default_config_loads_and_validates() -> None:
    cfg = load_scanner_config("config")
    assert isinstance(cfg, ScannerConfig)
    assert cfg.strategy.trend_template.min_rs_rank == 70
    assert cfg.strategy.trend_template.stricter_rs_rank == 80
    assert cfg.strategy.scoring.weights.fundamentals == 10.0
    assert cfg.strategy.scoring.fundamentals_max_weight == 15.0


def test_config_hash_deterministic() -> None:
    cfg1 = load_scanner_config("config")
    cfg2 = load_scanner_config("config")
    hash1 = compute_config_hash(cfg1)
    hash2 = compute_config_hash(cfg2)
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex string


def test_missing_config_dir_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="Configuration directory does not exist"):
        load_scanner_config("nonexistent_directory_12345")


def test_trend_template_stricter_rs_validation() -> None:
    # Stricter must be >= min_rs_rank
    with pytest.raises(ValidationError):
        TrendTemplateConfig(min_rs_rank=80, stricter_rs_rank=70)


def test_rs_weights_sum_validation() -> None:
    # Weights must sum to 1.0
    with pytest.raises(ValidationError):
        RSConfig(
            windows_days=[63, 126, 189, 252],
            weights=[0.40, 0.20, 0.20, 0.10],  # sum is 0.90
        )


def test_scoring_weights_sum_validation() -> None:
    # Component weights must sum to 100.0
    with pytest.raises(ValidationError):
        ScoringConfig(
            weights={
                "trend": 30.0,
                "vcp": 35.0,
                "volume": 15.0,
                "rs": 15.0,
                "fundamentals": 10.0,  # sum is 105.0
            }
        )


def test_fundamentals_weight_cap_validation() -> None:
    # Fundamentals cannot exceed fundamentals_max_weight
    with pytest.raises(ValidationError):
        ScoringConfig(
            weights={
                "trend": 20.0,
                "vcp": 35.0,
                "volume": 15.0,
                "rs": 10.0,
                "fundamentals": 20.0,
            },
            fundamentals_max_weight=15.0,
        )


def test_fundamentals_max_weight_hard_cap() -> None:
    # Hard safety rule: fundamentals_max_weight <= 15
    with pytest.raises(ValidationError):
        ScoringConfig(fundamentals_max_weight=20.0)


def test_normalization_bounds_worst_equals_best_rejection() -> None:
    # Normalization bounds worst cannot equal best
    with pytest.raises(ValidationError):
        NormalizationBounds(worst=10.0, best=10.0)


def test_vcp_contractions_range_validation() -> None:
    # contractions.max must be >= contractions.min
    with pytest.raises(ValidationError):
        VCPThresholdsConfig(contractions={"min": 4, "max": 2})


def test_classification_tier_hierarchy_validation() -> None:
    # A+ must be stricter than VCP
    with pytest.raises(ValidationError):
        ClassificationConfig(
            a_plus=TierClassificationConfig(min_contractions=2, max_final_contraction_pct=15.0),
            vcp=TierClassificationConfig(min_contractions=3, max_final_contraction_pct=10.0),
            vcp_like=TierClassificationConfig(min_contractions=2, max_final_contraction_pct=20.0),
        )


# ---------------------------------------------------------------------------
# Audit Fix 7: vcp / classification follow VCP_SPECIFICATION section 60 exactly
# ---------------------------------------------------------------------------

SPEC_60_VCP = {
    "contractions": {"min": 2, "max": 6},
    "swing": {"left_bars": 5, "right_bars": 5, "min_depth_pct": 2.0, "min_duration_days": 3,
              "short_swing_max_depth_pct": 4.0},
    "progressive_tolerance_pct": 10.0,
    "volatility": {"atr_period": 14, "contraction_ratio_max": 0.80, "measure": "true_range"},
    "volume": {"short_period": 5, "medium_period": 20, "long_period": 50, "dryup_ratio": 0.70},
    "pivot": {"max_distance_pct": 3.0, "right_side_window_days": 10,
              "max_right_side_range_pct": 5.0, "level_tolerance_pct": 1.5},
    "confirmation": {"allow_provisional_final_contraction": True,
                     "include_provisional_in_ranking": False},
    "prior_advance": {"enabled": True, "lookback_days": 120, "min_return_pct": 20.0},
    "base": {"max_duration_days": 130},
    "invalidation": {"trend_template_failure": True, "base_low_break_pct": 2.0,
                     "volatility_expansion_multiple": 2.0},
}  # fmt: skip

SPEC_60_CLASSIFICATION = {
    "a_plus": {"min_contractions": 3, "max_contractions": 6, "max_final_contraction_pct": 8.0,
               "require_progressive_tightening": True, "require_volume_dryup": True,
               "require_volatility_contraction": True, "require_tight_pivot": True},
    "vcp": {"min_contractions": 2, "max_contractions": 6, "max_final_contraction_pct": 12.0,
            "require_progressive_tightening": True, "require_volume_dryup": False,
            "require_volatility_contraction": False, "require_tight_pivot": True},
    "vcp_like": {"min_contractions": 2, "max_contractions": None,
                 "max_final_contraction_pct": 15.0,
                 "require_progressive_tightening": False, "require_volume_dryup": False,
                 "require_volatility_contraction": False, "require_tight_pivot": False},
}  # fmt: skip


def test_shipped_yaml_and_defaults_equal_spec_section_60() -> None:
    cfg = load_scanner_config("config").strategy
    assert cfg.vcp.model_dump() == SPEC_60_VCP
    assert cfg.classification.model_dump() == SPEC_60_CLASSIFICATION
    assert VCPThresholdsConfig().model_dump() == SPEC_60_VCP
    assert ClassificationConfig().model_dump() == SPEC_60_CLASSIFICATION


def test_spec_section_60_yaml_block_is_accepted_verbatim() -> None:
    """The exact YAML in VCP_SPECIFICATION section 60 must validate (no key is rejected)."""
    import re
    from pathlib import Path

    import yaml

    spec = (Path(__file__).resolve().parents[2] / "VCP_SPECIFICATION.md").read_text()
    section = spec[spec.index("# 60. Initial Configuration") :]
    block = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert block is not None
    data = yaml.safe_load(block.group(1))
    StrategyConfig(
        vcp=VCPThresholdsConfig(**data["vcp"]),
        classification=ClassificationConfig(**data["classification"]),
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"volume": {"short_period": 20, "medium_period": 20, "long_period": 50}},
        {"volume": {"dryup_ratio": 1.5}},
        {"volatility": {"contraction_ratio_max": 0}},
        {"swing": {"left_bars": 0}},
        {"pivot": {"max_distance_pct": -1}},
        {"unknown_key": 1},
    ],
)
def test_invalid_vcp_blocks_are_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        VCPThresholdsConfig(**bad)


def test_a_plus_cannot_drop_a_requirement_the_vcp_tier_has() -> None:
    tiers = {k: dict(v) for k, v in SPEC_60_CLASSIFICATION.items()}
    tiers["a_plus"]["require_tight_pivot"] = False
    with pytest.raises(ValidationError, match="require_tight_pivot"):
        ClassificationConfig(**tiers)


def test_tier_contraction_counts_must_fit_the_vcp_range() -> None:
    with pytest.raises(ValidationError, match="outside vcp.contractions"):
        StrategyConfig(vcp=VCPThresholdsConfig(contractions={"min": 3, "max": 6}))
    tiers = {k: dict(v) for k, v in SPEC_60_CLASSIFICATION.items()}
    tiers["a_plus"]["max_contractions"] = 8
    with pytest.raises(ValidationError, match="exceeds vcp.contractions.max"):
        StrategyConfig(classification=ClassificationConfig(**tiers))
