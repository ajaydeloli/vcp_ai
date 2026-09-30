"""Unit tests for the Gap Safety Net."""

from __future__ import annotations

from datetime import UTC, date, datetime

from vcp_scanner.data.reconciliation.gap_detector import GapDetector
from vcp_scanner.domain.corporate_actions import (
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType, DataQualityFlag
from vcp_scanner.domain.market import Candle


def _make_candle(d: date, o: float, c: float) -> Candle:
    return Candle(
        instrument_id="TEST",
        timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
        timeframe="D",
        open=o,
        high=max(o, c),
        low=min(o, c),
        close=c,
        volume=1000,
        provider="INTERNAL",
    )


def test_no_gap():
    detector = GapDetector()
    candles = [
        _make_candle(date(2024, 1, 1), 100.0, 105.0),
        _make_candle(date(2024, 1, 2), 106.0, 110.0),
    ]
    events = detector.detect(candles, [])
    assert len(events) == 0


def test_explained_gap():
    detector = GapDetector()
    candles = [
        _make_candle(date(2024, 1, 1), 100.0, 100.0),
        _make_candle(date(2024, 1, 2), 50.0, 50.0),  # 50% gap down
    ]

    # We have a resolution explaining the gap on the ex_date (Jan 2)
    resolutions = [
        CorporateActionResolution(
            resolution_id="RES1",
            instrument_id="TEST",
            action_type=CorporateActionType.SPLIT,
            status=CorporateActionStatus.CONFIRMED,
            ex_date=date(2024, 1, 2),
            ratio_numerator=2.0,
            ratio_denominator=1.0,
        )
    ]

    events = detector.detect(candles, resolutions)
    assert len(events) == 0


def test_unexplained_gap():
    detector = GapDetector()
    candles = [
        _make_candle(date(2024, 1, 1), 100.0, 100.0),
        _make_candle(date(2024, 1, 2), 50.0, 50.0),  # 50% gap down
    ]

    events = detector.detect(candles, [])
    assert len(events) == 1
    assert events[0].flag == DataQualityFlag.UNEXPLAINED_GAP
    assert events[0].context["is_split_like"] is True
    assert events[0].context["suspected_ratio"] == "2:1"


def test_gap_up_not_split_like():
    detector = GapDetector()
    candles = [
        _make_candle(date(2024, 1, 1), 100.0, 100.0),
        _make_candle(date(2024, 1, 2), 150.0, 150.0),  # 50% gap up
    ]

    events = detector.detect(candles, [])
    assert len(events) == 1
    assert events[0].flag == DataQualityFlag.UNEXPLAINED_GAP
    assert events[0].context["is_split_like"] is False


def test_dividend_on_the_ex_date_does_not_explain_a_split_like_gap():
    """Audit P0-3: a dividend never rescales prices, so it cannot account for a 50% drop."""
    detector = GapDetector()
    candles = [
        _make_candle(date(2024, 1, 1), 100.0, 100.0),
        _make_candle(date(2024, 1, 2), 50.0, 50.0),
    ]
    dividend = CorporateActionResolution(
        resolution_id="DIV",
        instrument_id="TEST",
        action_type=CorporateActionType.DIVIDEND,
        status=CorporateActionStatus.CONFIRMED,
        ex_date=date(2024, 1, 2),
        cash_amount=5.0,
    )
    events = detector.detect(candles, [dividend])
    assert len(events) == 1
    assert events[0].blocks_signal is True
