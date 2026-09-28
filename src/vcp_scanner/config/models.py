"""Configuration models and validation rules.

Implements all hypotheses and safety bounds specified in:
- PROJECT_DESIGN.md (sections 45-47, 60)
- TREND_TEMPLATE_SPECIFICATION.md (section 5)
- SCORING_SPECIFICATION.md (sections 8-9)
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictBaseModel(BaseModel):
    """Base model prohibiting extra fields and validating on assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class TrendTemplateConfig(StrictBaseModel):
    """TREND_TEMPLATE_SPECIFICATION section 5."""

    required: bool = True
    min_rs_rank: Annotated[int, Field(ge=1, le=99)] = 70
    stricter_rs_rank: Annotated[int, Field(ge=1, le=99)] = 80
    sma200_slope_lookback_days: Annotated[int, Field(ge=21)] = 21
    min_above_52w_low_pct: Annotated[float, Field(gt=0)] = 25.0
    max_below_52w_high_pct: Annotated[float, Field(gt=0)] = 25.0
    extreme_basis: Literal["high_low", "close"] = "high_low"

    @model_validator(mode="after")
    def validate_stricter_rs(self) -> TrendTemplateConfig:
        if self.stricter_rs_rank < self.min_rs_rank:
            raise ValueError(
                f"stricter_rs_rank ({self.stricter_rs_rank}) must be >= "
                f"min_rs_rank ({self.min_rs_rank})"
            )
        return self


class RSConfig(StrictBaseModel):
    """TREND_TEMPLATE_SPECIFICATION section 3, 5 (rs-1.0.0)."""

    version: str = "rs-1.0.0"
    windows_days: list[int] = Field(default_factory=lambda: [63, 126, 189, 252])
    weights: list[float] = Field(default_factory=lambda: [0.40, 0.20, 0.20, 0.20])
    min_history_days: Annotated[int, Field(ge=253)] = 253

    @model_validator(mode="after")
    def validate_weights(self) -> RSConfig:
        if len(self.windows_days) != len(self.weights):
            raise ValueError(
                f"windows_days length ({len(self.windows_days)}) must match "
                f"weights length ({len(self.weights)})"
            )
        total_weight = sum(self.weights)
        if not math.isclose(total_weight, 1.0, rel_tol=1e-5):
            raise ValueError(f"RS weights must sum to 1.0, got {total_weight}")
        return self


class StageConfig(StrictBaseModel):
    """TREND_TEMPLATE_SPECIFICATION section 4, 5 (stage-1.0.0)."""

    sma_weeks: Annotated[int, Field(gt=0)] = 30
    slope_lookback_weeks: Annotated[int, Field(gt=0)] = 4
    flat_band_pct: Annotated[float, Field(ge=0)] = 0.5
    prior_advance_min_pct: Annotated[float, Field(ge=0)] = 10.0


class VCPThresholdsConfig(StrictBaseModel):
    """PROJECT_DESIGN section 46."""

    min_contractions: Annotated[int, Field(ge=2)] = 2
    max_contractions: Annotated[int, Field(ge=2)] = 6
    progressive_tolerance_pct: Annotated[float, Field(ge=0)] = 10.0
    require_volume_dryup: bool = True
    require_tight_pivot: bool = True

    @model_validator(mode="after")
    def validate_contractions_range(self) -> VCPThresholdsConfig:
        if self.max_contractions < self.min_contractions:
            raise ValueError(
                f"max_contractions ({self.max_contractions}) must be >= "
                f"min_contractions ({self.min_contractions})"
            )
        return self


class TierClassificationConfig(StrictBaseModel):
    min_contractions: Annotated[int, Field(ge=1)]
    max_final_contraction_pct: Annotated[float, Field(gt=0)]


class ClassificationConfig(StrictBaseModel):
    """PROJECT_DESIGN section 46."""

    a_plus: TierClassificationConfig = Field(
        default_factory=lambda: TierClassificationConfig(
            min_contractions=3, max_final_contraction_pct=8.0
        )
    )
    vcp: TierClassificationConfig = Field(
        default_factory=lambda: TierClassificationConfig(
            min_contractions=2, max_final_contraction_pct=12.0
        )
    )
    vcp_like: TierClassificationConfig = Field(
        default_factory=lambda: TierClassificationConfig(
            min_contractions=2, max_final_contraction_pct=15.0
        )
    )

    @model_validator(mode="after")
    def validate_tiers(self) -> ClassificationConfig:
        if self.a_plus.min_contractions < self.vcp.min_contractions:
            raise ValueError("A+ min_contractions must be >= VCP min_contractions")
        if self.a_plus.max_final_contraction_pct > self.vcp.max_final_contraction_pct:
            raise ValueError(
                f"A+ max_final_contraction_pct ({self.a_plus.max_final_contraction_pct}) "
                f"must be <= VCP ({self.vcp.max_final_contraction_pct})"
            )
        if self.vcp.max_final_contraction_pct > self.vcp_like.max_final_contraction_pct:
            raise ValueError(
                f"VCP max_final_contraction_pct ({self.vcp.max_final_contraction_pct}) "
                f"must be <= VCP_LIKE ({self.vcp_like.max_final_contraction_pct})"
            )
        return self


class ScoringWeights(StrictBaseModel):
    """SCORING_SPECIFICATION section 1: trend, vcp, volume, rs, fundamentals."""

    trend: Annotated[float, Field(ge=0)] = 25.0
    vcp: Annotated[float, Field(ge=0)] = 35.0
    volume: Annotated[float, Field(ge=0)] = 15.0
    rs: Annotated[float, Field(ge=0)] = 15.0
    fundamentals: Annotated[float, Field(ge=0)] = 10.0


class NormalizationBounds(StrictBaseModel):
    """worst -> best bounds (SCORING_SPECIFICATION section 2). worst != best."""

    worst: float
    best: float

    @model_validator(mode="after")
    def validate_bounds(self) -> NormalizationBounds:
        if math.isclose(self.worst, self.best, rel_tol=1e-7):
            raise ValueError(f"worst ({self.worst}) cannot equal best ({self.best})")
        return self


class TrendScoreComponents(StrictBaseModel):
    """SCORING_SPECIFICATION section 3."""

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "high_proximity": 40.0,
            "sma200_slope": 30.0,
            "ma_stack_margin": 30.0,
        }
    )
    bounds: dict[str, NormalizationBounds] = Field(
        default_factory=lambda: {
            "high_proximity": NormalizationBounds(worst=25.0, best=0.0),
            "sma200_slope": NormalizationBounds(worst=0.0, best=8.0),
            "ma_stack_margin": NormalizationBounds(worst=0.0, best=20.0),
        }
    )

    @model_validator(mode="after")
    def validate_weights_and_bounds(self) -> TrendScoreComponents:
        total = sum(self.weights.values())
        if not math.isclose(total, 100.0, rel_tol=1e-5):
            raise ValueError(f"Trend sub-component weights must sum to 100, got {total}")
        if set(self.weights.keys()) != set(self.bounds.keys()):
            raise ValueError("Trend sub-component weights keys must match bounds keys")
        return self


class VCPScoreComponents(StrictBaseModel):
    """SCORING_SPECIFICATION section 4."""

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "contraction_sequence": 25.0,
            "tightening": 20.0,
            "final_contraction": 20.0,
            "volatility": 15.0,
            "pivot": 10.0,
            "base_structure": 10.0,
        }
    )
    bounds: dict[str, NormalizationBounds] = Field(
        default_factory=lambda: {
            "contraction_sequence": NormalizationBounds(worst=2.0, best=4.0),
            "tightening": NormalizationBounds(worst=1.10, best=0.60),
            "final_contraction": NormalizationBounds(worst=15.0, best=3.0),
            "volatility": NormalizationBounds(worst=1.0, best=0.5),
            "pivot": NormalizationBounds(worst=8.0, best=2.0),
            "base_structure": NormalizationBounds(worst=40.0, best=15.0),
        }
    )

    @model_validator(mode="after")
    def validate_weights_and_bounds(self) -> VCPScoreComponents:
        total = sum(self.weights.values())
        if not math.isclose(total, 100.0, rel_tol=1e-5):
            raise ValueError(f"VCP sub-component weights must sum to 100, got {total}")
        if set(self.weights.keys()) != set(self.bounds.keys()):
            raise ValueError("VCP sub-component weights keys must match bounds keys")
        return self


class VolumeScoreComponents(StrictBaseModel):
    """SCORING_SPECIFICATION section 5."""

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "dryup_quality": 50.0,
            "up_down_volume": 25.0,
            "distribution": 25.0,
        }
    )
    bounds: dict[str, NormalizationBounds] = Field(
        default_factory=lambda: {
            "dryup_quality": NormalizationBounds(worst=1.0, best=0.40),
            "up_down_volume": NormalizationBounds(worst=0.8, best=1.5),
            "distribution": NormalizationBounds(worst=4.0, best=0.0),
        }
    )

    @model_validator(mode="after")
    def validate_weights_and_bounds(self) -> VolumeScoreComponents:
        total = sum(self.weights.values())
        if not math.isclose(total, 100.0, rel_tol=1e-5):
            raise ValueError(f"Volume sub-component weights must sum to 100, got {total}")
        if set(self.weights.keys()) != set(self.bounds.keys()):
            raise ValueError("Volume sub-component weights keys must match bounds keys")
        return self


class FundamentalScoreComponents(StrictBaseModel):
    """SCORING_SPECIFICATION section 7."""

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "eps_yoy": 30.0,
            "eps_qoq": 20.0,
            "sales_yoy": 20.0,
            "eps_acceleration": 10.0,
            "margin_expansion": 10.0,
            "roe": 5.0,
            "debt": 5.0,
        }
    )
    bounds: dict[str, NormalizationBounds] = Field(
        default_factory=lambda: {
            "eps_yoy": NormalizationBounds(worst=0.0, best=50.0),
            "eps_qoq": NormalizationBounds(worst=0.0, best=50.0),
            "sales_yoy": NormalizationBounds(worst=0.0, best=30.0),
            "eps_acceleration": NormalizationBounds(worst=0.0, best=15.0),
            "margin_expansion": NormalizationBounds(worst=0.0, best=3.0),
            "roe": NormalizationBounds(worst=10.0, best=25.0),
            "debt": NormalizationBounds(worst=1.0, best=0.0),
        }
    )

    @model_validator(mode="after")
    def validate_weights_and_bounds(self) -> FundamentalScoreComponents:
        total = sum(self.weights.values())
        if not math.isclose(total, 100.0, rel_tol=1e-5):
            raise ValueError(f"Fundamental sub-component weights must sum to 100, got {total}")
        if set(self.weights.keys()) != set(self.bounds.keys()):
            raise ValueError("Fundamental sub-component weights keys must match bounds keys")
        return self


class ScoreSubComponentsConfig(StrictBaseModel):
    trend: TrendScoreComponents = Field(default_factory=TrendScoreComponents)
    vcp: VCPScoreComponents = Field(default_factory=VCPScoreComponents)
    volume: VolumeScoreComponents = Field(default_factory=VolumeScoreComponents)
    fundamentals: FundamentalScoreComponents = Field(default_factory=FundamentalScoreComponents)


class ScoringConfig(StrictBaseModel):
    """SCORING_SPECIFICATION section 8."""

    version: str = "scoring-1.0.0"
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    fundamentals_max_weight: Annotated[float, Field(le=15.0, ge=0.0)] = 15.0
    fundamentals_min_availability: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    high_volume_multiple: Annotated[float, Field(gt=0)] = 1.5
    components: ScoreSubComponentsConfig = Field(default_factory=ScoreSubComponentsConfig)

    @model_validator(mode="after")
    def validate_overall_scoring(self) -> ScoringConfig:
        w = self.weights
        total = w.trend + w.vcp + w.volume + w.rs + w.fundamentals
        if not math.isclose(total, 100.0, rel_tol=1e-5):
            raise ValueError(f"Scoring component weights must sum to 100.0, got {total}")
        if w.fundamentals > self.fundamentals_max_weight:
            raise ValueError(
                f"Fundamentals weight ({w.fundamentals}) exceeds maximum allowed "
                f"cap ({self.fundamentals_max_weight})"
            )
        return self


class UniverseConfig(StrictBaseModel):
    """Universe filters and exclusions (PROJECT_DESIGN sections 14A, 45)."""

    exchange: str = "NSE"
    min_close_price: Annotated[float, Field(ge=0)] = 10.0
    min_daily_turnover_inr: Annotated[float, Field(ge=0)] = 5_000_000.0  # 50 Lakhs
    min_history_days: Annotated[int, Field(ge=253)] = 253
    exclude_asm_gsm: bool = True
    exclude_trade_to_trade: bool = True
    eligible_series: list[str] = Field(default_factory=lambda: ["EQ", "BE"])


class DataConfig(StrictBaseModel):
    """Data persistence and provider configuration (PROJECT_DESIGN section 45)."""

    duckdb_path: str = "data/vcp_scanner.duckdb"
    raw_storage_dir: str = "data/raw"
    canonical_storage_dir: str = "data/canonical"
    primary_provider: str = "kite"
    secondary_provider: str | None = "dhan"
    trading_calendar: str = "NSE"


class MonitoringConfig(StrictBaseModel):
    """Monitoring and event engine configuration (PROJECT_DESIGN sections 45, 53)."""

    poll_interval_seconds: Annotated[int, Field(gt=0)] = 60
    enabled_events: list[str] = Field(
        default_factory=lambda: [
            "VCP_FORMING",
            "VCP_QUALIFIED",
            "PIVOT_READY",
            "PIVOT_CHANGED",
            "BREAKOUT",
            "BREAKOUT_FAILED",
            "SETUP_INVALIDATED",
            "DATA_STALE",
        ]
    )
    channels: list[str] = Field(default_factory=lambda: ["console"])


class LoggingConfig(StrictBaseModel):
    """Structured logging configuration (PROJECT_DESIGN sections 45, 76)."""

    level: str = "INFO"
    json_format: bool = False
    log_file: str | None = None


class StrategyConfig(StrictBaseModel):
    """Unified strategy specification configuration (PROJECT_DESIGN section 46)."""

    trend_template: TrendTemplateConfig = Field(default_factory=TrendTemplateConfig)
    rs: RSConfig = Field(default_factory=RSConfig)
    stage: StageConfig = Field(default_factory=StageConfig)
    vcp: VCPThresholdsConfig = Field(default_factory=VCPThresholdsConfig)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


class ScannerConfig(StrictBaseModel):
    """Master application configuration combining all sub-configurations."""

    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
