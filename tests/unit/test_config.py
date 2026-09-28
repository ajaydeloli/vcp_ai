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
    # max_contractions must be >= min_contractions
    with pytest.raises(ValidationError):
        VCPThresholdsConfig(min_contractions=4, max_contractions=2)


def test_classification_tier_hierarchy_validation() -> None:
    # A+ must be stricter than VCP
    with pytest.raises(ValidationError):
        ClassificationConfig(
            a_plus=TierClassificationConfig(min_contractions=2, max_final_contraction_pct=15.0),
            vcp=TierClassificationConfig(min_contractions=3, max_final_contraction_pct=10.0),
            vcp_like=TierClassificationConfig(min_contractions=2, max_final_contraction_pct=20.0),
        )
