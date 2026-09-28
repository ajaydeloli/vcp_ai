from datetime import UTC, date, datetime

from vcp_scanner.domain.corporate_actions import (
    CorporateAction,
    CorporateActionAdjustment,
    CorporateActionResolution,
    CorporateActionStatus,
    CorporateActionType,
)


def test_corporate_action_creation():
    ca = CorporateAction(
        corporate_action_id="CA-1",
        instrument_id="RELIANCE",
        action_type=CorporateActionType.SPLIT,
        source="NSE",
        created_at=datetime.now(UTC),
        ex_date=date(2024, 5, 1),
        ratio_numerator=2.0,
        ratio_denominator=1.0,
    )
    assert ca.action_type == CorporateActionType.SPLIT
    assert ca.ratio_numerator == 2.0


def test_corporate_action_resolution_creation():
    car = CorporateActionResolution(
        resolution_id="RES-1",
        instrument_id="RELIANCE",
        action_type=CorporateActionType.SPLIT,
        status=CorporateActionStatus.CONFIRMED,
        ex_date=date(2024, 5, 1),
        ratio_numerator=2.0,
        ratio_denominator=1.0,
    )
    assert car.status == CorporateActionStatus.CONFIRMED


def test_corporate_action_adjustment_creation():
    caa = CorporateActionAdjustment(
        resolution_id="RES-1",
        instrument_id="RELIANCE",
        effective_date=date(2024, 5, 1),
        price_factor=0.5,
        volume_factor=2.0,
        cumulative_price_factor=0.5,
        cumulative_volume_factor=2.0,
        source="NSE",
        calculation_version="1.0",
    )
    assert caa.price_factor == 0.5
