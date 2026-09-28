"""Unit tests for domain objects, enums, and properties (AGENTS.md hard rules 4, 5, 8)."""

import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from vcp_scanner.domain.enums import (
    ConfirmationState,
    CorporateActionType,
    ErrorCategory,
    SurvivorshipStatus,
    Timeframe,
    TrendTemplateStatus,
    VCPClassification,
    VCPStatus,
    WeeklyStage,
)
from vcp_scanner.domain.errors import (
    ConfigError,
    DataValidationError,
    ProviderError,
    StorageError,
)
from vcp_scanner.domain.fundamentals import FundamentalSnapshot
from vcp_scanner.domain.market import (
    Candle,
    CorporateAction,
    Instrument,
)
from vcp_scanner.domain.trend import (
    TREND_CONDITION_NAMES,
    RelativeStrengthResult,
    TrendConditionResult,
    TrendTemplateResult,
    WeeklyContext,
)
from vcp_scanner.domain.vcp import Contraction, PivotCandidate, VCPPattern


def test_vcp_classification_order_and_production() -> None:
    assert VCPClassification.NONE.rank < VCPClassification.VCP_LIKE.rank
    assert VCPClassification.VCP_LIKE.rank < VCPClassification.VCP.rank
    assert VCPClassification.VCP.rank < VCPClassification.A_PLUS_VCP.rank

    assert not VCPClassification.NONE.is_production
    assert not VCPClassification.VCP_LIKE.is_production
    assert VCPClassification.VCP.is_production
    assert VCPClassification.A_PLUS_VCP.is_production

    assert VCPClassification.A_PLUS_VCP.at_least(VCPClassification.VCP)
    assert VCPClassification.VCP.at_least(VCPClassification.VCP)
    assert not VCPClassification.VCP_LIKE.at_least(VCPClassification.VCP)


def test_vcp_status_data_state_discrimination() -> None:
    # AGENTS.md rule 4: missing is not zero / data states distinct from no VCP
    assert VCPStatus.INSUFFICIENT_DATA.is_data_state
    assert VCPStatus.DATA_NOT_READY.is_data_state
    assert VCPStatus.STALE_DATA.is_data_state
    assert not VCPStatus.FORMING.is_data_state
    assert not VCPStatus.FAILED.is_data_state


def test_survivorship_status_validation_gate() -> None:
    # AGENTS.md rule 8: Only POINT_IN_TIME_COMPLETE may validate thresholds
    assert SurvivorshipStatus.POINT_IN_TIME_COMPLETE.may_validate_thresholds
    assert not SurvivorshipStatus.PARTIAL.may_validate_thresholds
    assert not SurvivorshipStatus.BIASED.may_validate_thresholds


def test_weekly_stage_stage2_check() -> None:
    ctx2 = WeeklyContext(
        instrument_id="TEST",
        as_of_date=date(2025, 1, 10),
        weekly_stage=WeeklyStage.STAGE_2,
        sma_w=150.0,
        slope_pct=1.2,
        prior_pct=12.0,
        is_partial_week=False,
        algorithm_version="stage-1.0.0",
    )
    assert ctx2.weekly_stage2_pass

    ctx_trans = WeeklyContext(
        instrument_id="TEST",
        as_of_date=date(2025, 1, 10),
        weekly_stage=WeeklyStage.TRANSITION,
        sma_w=150.0,
        slope_pct=0.1,
        prior_pct=5.0,
        is_partial_week=False,
        algorithm_version="stage-1.0.0",
    )
    assert not ctx_trans.weekly_stage2_pass


def test_trend_template_result_passed_property() -> None:
    conds = tuple(
        TrendConditionResult(
            condition_id=i + 1,
            name=name,
            measurement=100.0,
            threshold=90.0,
            passed=True,
        )
        for i, name in enumerate(TREND_CONDITION_NAMES)
    )

    passed_res = TrendTemplateResult(
        instrument_id="TEST",
        as_of_date=date(2025, 1, 10),
        status=TrendTemplateStatus.PASS,
        conditions=conds,
        algorithm_version="trend-1.0.0",
    )
    assert passed_res.passed

    # Failing status
    failed_res = TrendTemplateResult(
        instrument_id="TEST",
        as_of_date=date(2025, 1, 10),
        status=TrendTemplateStatus.FAIL,
        conditions=conds,
        algorithm_version="trend-1.0.0",
    )
    assert not failed_res.passed

    # One condition failed
    one_failed_conds = list(conds)
    one_failed_conds[0] = TrendConditionResult(
        condition_id=1,
        name=TREND_CONDITION_NAMES[0],
        measurement=80.0,
        threshold=90.0,
        passed=False,
    )
    res_with_failure = TrendTemplateResult(
        instrument_id="TEST",
        as_of_date=date(2025, 1, 10),
        status=TrendTemplateStatus.PASS,
        conditions=tuple(one_failed_conds),
        algorithm_version="trend-1.0.0",
    )
    assert not res_with_failure.passed


def test_domain_dataclasses_frozen() -> None:
    inst = Instrument(instrument_id="123", symbol="RELIANCE")
    with pytest.raises(dataclasses.FrozenInstanceError):
        inst.symbol = "TCS"  # type: ignore[misc]

    candle = Candle(
        instrument_id="123",
        timestamp=datetime.now(UTC),
        timeframe=Timeframe.DAILY,
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=10000,
        provider="kite",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        candle.close = 104.0  # type: ignore[misc]


def test_domain_models_creation() -> None:
    corp = CorporateAction(
        action_id="act_1",
        instrument_id="inst_1",
        action_type=CorporateActionType.SPLIT,
        ex_date=date(2025, 1, 10),
        source="NSE",
        ratio_numerator=Decimal(2),
        ratio_denominator=Decimal(1),
    )
    assert corp.ratio_numerator == Decimal(2)

    fund = FundamentalSnapshot(
        instrument_id="inst_1",
        period_end=date(2024, 12, 31),
        publication_date=date(2025, 1, 15),
        available_at=datetime.now(UTC),
        source="bse",
        retrieved_at=datetime.now(UTC),
        metrics={"eps_yoy": 25.5},
    )
    assert fund.metrics["eps_yoy"] == 25.5

    rs = RelativeStrengthResult(
        instrument_id="inst_1",
        as_of_date=date(2025, 1, 10),
        return_63d=0.15,
        return_126d=0.25,
        return_189d=0.35,
        return_252d=0.45,
        rs_raw=0.25,
        rs_rank=85,
        population_size=1500,
        calculation_version="rs-1.0.0",
    )
    assert rs.rs_rank == 85

    c1 = Contraction(
        index=1,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 15),
        peak_price=120.0,
        trough_price=105.0,
        depth_pct=12.5,
        duration_days=10,
        atr_pct=2.1,
        volume_ratio=0.8,
    )
    pivot = PivotCandidate(pivot_price=120.0, pivot_date=date(2025, 1, 15), kind="CONFIRMED")
    vcp = VCPPattern(
        instrument_id="inst_1",
        as_of_date=date(2025, 1, 15),
        base_start=date(2025, 1, 1),
        base_end=date(2025, 1, 15),
        base_high=120.0,
        base_low=105.0,
        contractions=(c1,),
        progressive_tightening=True,
        tightening_quality=0.8,
        volatility_quality=0.7,
        volume_quality=0.9,
        pivot_quality=0.85,
        pivot=pivot,
        final_contraction_pct=5.5,
        pivot_distance_pct=1.2,
        classification=VCPClassification.VCP,
        status=VCPStatus.PIVOT_READY,
        confirmation_state=ConfirmationState.CONFIRMED,
        algorithm_version="vcp-1.0.0",
    )
    assert vcp.classification == VCPClassification.VCP


def test_error_categories() -> None:
    err = ConfigError("Invalid key")
    assert err.category == ErrorCategory.CONFIG_ERROR

    err_val = DataValidationError("Bad candle")
    assert err_val.category == ErrorCategory.DATA_VALIDATION_ERROR

    err_prov = ProviderError("Timeout")
    assert err_prov.category == ErrorCategory.PROVIDER_ERROR

    err_storage = StorageError("Disk full")
    assert err_storage.category == ErrorCategory.STORAGE_ERROR
