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

    # Gate policy for downstream consumers (scanner / VCP / scoring): when True, instruments
    # whose trend template status is not PASS are excluded. The engine itself always
    # evaluates and stores every instrument, so it does not read this flag.
    required: bool = True
    min_rs_rank: Annotated[int, Field(ge=1, le=99)] = 70
    # Research threshold: exposed as TrendTemplateResult.meets_stricter_rs, not a condition.
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
    # An instrument whose latest bar is older than this many calendar days before the
    # as-of date is not ranked (a suspended/stale name must not carry an old return into
    # today's percentile). 4 covers a long weekend plus one holiday.
    max_staleness_days: Annotated[int, Field(ge=0)] = 4

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


class VCPContractionCountConfig(StrictBaseModel):
    """VCP_SPECIFICATION section 60 ``vcp.contractions``."""

    min: Annotated[int, Field(ge=1)] = 2
    max: Annotated[int, Field(ge=1)] = 6

    @model_validator(mode="after")
    def validate_range(self) -> VCPContractionCountConfig:
        if self.max < self.min:
            raise ValueError(f"contractions.max ({self.max}) must be >= min ({self.min})")
        return self


class VCPSwingConfig(StrictBaseModel):
    """VCP_SPECIFICATION sections 9, 23, 60 ``vcp.swing``."""

    left_bars: Annotated[int, Field(ge=1)] = 5
    right_bars: Annotated[int, Field(ge=1)] = 5
    min_depth_pct: Annotated[float, Field(gt=0, lt=100)] = 2.0
    min_duration_days: Annotated[int, Field(ge=1)] = 3


class VCPVolatilityConfig(StrictBaseModel):
    """VCP_SPECIFICATION sections 15, 18A, 60 ``vcp.volatility``."""

    atr_period: Annotated[int, Field(ge=2)] = 14
    contraction_ratio_max: Annotated[float, Field(gt=0, le=1)] = 0.80


class VCPVolumeConfig(StrictBaseModel):
    """VCP_SPECIFICATION sections 17, 18A, 60 ``vcp.volume``."""

    short_period: Annotated[int, Field(ge=1)] = 5
    medium_period: Annotated[int, Field(ge=1)] = 20
    long_period: Annotated[int, Field(ge=1)] = 50
    dryup_ratio: Annotated[float, Field(gt=0, le=1)] = 0.70

    @model_validator(mode="after")
    def validate_periods(self) -> VCPVolumeConfig:
        if not self.short_period < self.medium_period < self.long_period:
            raise ValueError(
                "volume periods must satisfy short_period < medium_period < long_period, got "
                f"{self.short_period}, {self.medium_period}, {self.long_period}"
            )
        return self


class VCPPivotConfig(StrictBaseModel):
    """VCP_SPECIFICATION sections 19-22, 18A, 60 ``vcp.pivot``."""

    max_distance_pct: Annotated[float, Field(gt=0)] = 3.0
    right_side_window_days: Annotated[int, Field(ge=1)] = 10
    max_right_side_range_pct: Annotated[float, Field(gt=0)] = 5.0


class VCPConfirmationConfig(StrictBaseModel):
    """VCP_SPECIFICATION sections 9A, 27, 60 ``vcp.confirmation``."""

    allow_provisional_final_contraction: bool = True
    include_provisional_in_ranking: bool = False


class VCPThresholdsConfig(StrictBaseModel):
    """VCP_SPECIFICATION section 60 ``vcp`` block (shape adopted verbatim, audit Fix 7).

    Values are initial hypotheses (Phase 6B validates them). The detector is Phase 6; this
    model only fixes the configuration contract it will read.
    """

    contractions: VCPContractionCountConfig = Field(default_factory=VCPContractionCountConfig)
    swing: VCPSwingConfig = Field(default_factory=VCPSwingConfig)
    # Relative tolerance: D(n+1) <= D(n) x (1 + pct/100) (VCP_SPECIFICATION section 13).
    progressive_tolerance_pct: Annotated[float, Field(ge=0)] = 10.0
    volatility: VCPVolatilityConfig = Field(default_factory=VCPVolatilityConfig)
    volume: VCPVolumeConfig = Field(default_factory=VCPVolumeConfig)
    pivot: VCPPivotConfig = Field(default_factory=VCPPivotConfig)
    confirmation: VCPConfirmationConfig = Field(default_factory=VCPConfirmationConfig)


class TierClassificationConfig(StrictBaseModel):
    """One classification tier (VCP_SPECIFICATION sections 18A, 28-31, 60).

    ``require_*`` flags reference the operational definitions in section 18A; an omitted flag
    means the tier does not require that criterion. ``max_contractions`` None = no upper bound
    beyond ``vcp.contractions.max``.
    """

    min_contractions: Annotated[int, Field(ge=1)]
    max_final_contraction_pct: Annotated[float, Field(gt=0, lt=100)]
    max_contractions: Annotated[int, Field(ge=1)] | None = None
    require_progressive_tightening: bool = False
    require_volume_dryup: bool = False
    require_volatility_contraction: bool = False
    require_tight_pivot: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> TierClassificationConfig:
        if self.max_contractions is not None and self.max_contractions < self.min_contractions:
            raise ValueError(
                f"max_contractions ({self.max_contractions}) must be >= "
                f"min_contractions ({self.min_contractions})"
            )
        return self

    def requirements(self) -> dict[str, bool]:
        return {
            "require_progressive_tightening": self.require_progressive_tightening,
            "require_volume_dryup": self.require_volume_dryup,
            "require_volatility_contraction": self.require_volatility_contraction,
            "require_tight_pivot": self.require_tight_pivot,
        }


class ClassificationConfig(StrictBaseModel):
    """VCP_SPECIFICATION section 60 ``classification`` block."""

    a_plus: TierClassificationConfig = Field(
        default_factory=lambda: TierClassificationConfig(
            min_contractions=3,
            max_contractions=6,
            max_final_contraction_pct=8.0,
            require_progressive_tightening=True,
            require_volume_dryup=True,
            require_volatility_contraction=True,
            require_tight_pivot=True,
        )
    )
    vcp: TierClassificationConfig = Field(
        default_factory=lambda: TierClassificationConfig(
            min_contractions=2,
            max_contractions=6,
            max_final_contraction_pct=12.0,
            require_progressive_tightening=True,
            require_tight_pivot=True,
        )
    )
    vcp_like: TierClassificationConfig = Field(
        default_factory=lambda: TierClassificationConfig(
            min_contractions=2, max_final_contraction_pct=15.0
        )
    )

    @model_validator(mode="after")
    def validate_tiers(self) -> ClassificationConfig:
        """A stricter tier may never be looser than the tier below it on any criterion."""
        for strict_name, loose_name in (("a_plus", "vcp"), ("vcp", "vcp_like")):
            strict: TierClassificationConfig = getattr(self, strict_name)
            loose: TierClassificationConfig = getattr(self, loose_name)
            if strict.min_contractions < loose.min_contractions:
                raise ValueError(
                    f"{strict_name}.min_contractions must be >= {loose_name}.min_contractions"
                )
            if strict.max_final_contraction_pct > loose.max_final_contraction_pct:
                raise ValueError(
                    f"{strict_name}.max_final_contraction_pct "
                    f"({strict.max_final_contraction_pct}) must be <= {loose_name} "
                    f"({loose.max_final_contraction_pct})"
                )
            for flag, required in loose.requirements().items():
                if required and not strict.requirements()[flag]:
                    raise ValueError(f"{loose_name} sets {flag} but {strict_name} does not")
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
    # Defaults follow PROJECT_DESIGN §14 (min price 20, 1 crore average traded value on both
    # the 20d and 50d windows, raw close x raw volume). Change them via a strategy-version bump.
    min_close_price: Annotated[float, Field(ge=0)] = 20.0
    min_daily_turnover_inr: Annotated[float, Field(ge=0)] = 10_000_000.0  # 20d avg, 1 crore
    min_avg_traded_value_50d_inr: Annotated[float, Field(ge=0)] = 10_000_000.0  # 50d avg
    min_history_days: Annotated[int, Field(ge=253)] = 253
    # Latest bar older than this many calendar days before the as-of date => not trading.
    max_staleness_days: Annotated[int, Field(ge=0)] = 30
    exclude_asm_gsm: bool = True
    exclude_trade_to_trade: bool = True
    # EQ only: BE (trade-to-trade) and SME series (SM/ST) are excluded by default (§14).
    eligible_series: list[str] = Field(default_factory=lambda: ["EQ"])


class UnexplainedGapConfig(StrictBaseModel):
    gap_pct: Annotated[float, Field(gt=0)] = 30.0
    split_like_max_integer: Annotated[int, Field(gt=0)] = 10
    split_like_tolerance_pct: Annotated[float, Field(ge=0)] = 3.0


class CorporateActionsConfig(StrictBaseModel):
    primary_source: str = "nse"
    secondary_source: str = "upstox"
    secondary_grace_days: Annotated[int, Field(ge=0)] = 3
    conflict_blocks_signals: bool = True
    unexplained_gap: UnexplainedGapConfig = Field(default_factory=UnexplainedGapConfig)


class CompletenessConfig(StrictBaseModel):
    """How market sessions are observed for daily-bar completeness checks (audit P0-4)."""

    # A date is a market session when this share of the instruments trading around it have a bar.
    min_breadth: Annotated[float, Field(gt=0, le=1)] = 0.5
    # Below this many trading instruments the cross-section is too thin to infer sessions.
    min_active_instruments: Annotated[int, Field(ge=1)] = 5


class DataConfig(StrictBaseModel):
    """Data persistence and provider configuration (PROJECT_DESIGN section 45)."""

    duckdb_path: str = "data/vcp_scanner.duckdb"
    raw_storage_dir: str = "data/raw"
    canonical_storage_dir: str = "data/canonical"
    primary_provider: str = "kite"
    secondary_provider: str | None = "upstox"
    trading_calendar: str = "NSE"
    completeness: CompletenessConfig = Field(default_factory=CompletenessConfig)
    corporate_actions: CorporateActionsConfig = Field(default_factory=CorporateActionsConfig)


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

    @model_validator(mode="after")
    def validate_tiers_within_contraction_range(self) -> StrategyConfig:
        """Every tier's contraction counts must lie inside ``vcp.contractions``."""
        lo, hi = self.vcp.contractions.min, self.vcp.contractions.max
        for name in ("a_plus", "vcp", "vcp_like"):
            tier: TierClassificationConfig = getattr(self.classification, name)
            if tier.min_contractions < lo or tier.min_contractions > hi:
                raise ValueError(
                    f"classification.{name}.min_contractions ({tier.min_contractions}) is "
                    f"outside vcp.contractions [{lo}, {hi}]"
                )
            if tier.max_contractions is not None and tier.max_contractions > hi:
                raise ValueError(
                    f"classification.{name}.max_contractions ({tier.max_contractions}) "
                    f"exceeds vcp.contractions.max ({hi})"
                )
        return self


class ScannerConfig(StrictBaseModel):
    """Master application configuration combining all sub-configurations."""

    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
