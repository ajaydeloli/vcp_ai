"""Gap safety net: an action explains a gap when its ex-date falls after the previous bar and
up to the gap bar, and only if it accounts for the gap's size (audit 2.7c).

Prices are real NSE bhavcopy closes/opens around the dates named in each test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from vcp_scanner.data.reconciliation.gap_detector import GapDetector
from vcp_scanner.domain.corporate_actions import CorporateActionResolution, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType as T
from vcp_scanner.domain.market import Candle

AT = datetime(2026, 10, 1, tzinfo=UTC)


def _bar(d: date, o: float, c: float) -> Candle:
    return Candle(
        instrument_id="X", timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC), timeframe="D",
        open=o, high=max(o, c), low=min(o, c), close=c, volume=1000, provider="NSE_BHAVCOPY",
    )  # fmt: skip


def _res(
    kind: T, ex: date, num: float | None, den: float | None, cash: float | None = None
) -> CorporateActionResolution:
    return CorporateActionResolution(
        f"r-{kind}-{ex}", "X", kind, CorporateActionStatus.SINGLE_SOURCE, ex_date=ex,
        ratio_numerator=num, ratio_denominator=den, cash_amount=cash,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("name", "prev", "curr", "action"),
    [
        # Split Rs 10 -> Re 1 on 2024-01-25; DOLPHIN (BE) did not trade until 2024-01-29.
        ("DOLPHIN split", (date(2024, 1, 24), 1830.6), (date(2024, 1, 29), 186.7),
         _res(T.SPLIT, date(2024, 1, 25), 10, 1)),
        # Bonus 2:1 on Friday 2025-10-10 with no UEL bar that day.
        ("UEL bonus", (date(2025, 10, 9), 371.35), (date(2025, 10, 13), 117.65),
         _res(T.BONUS, date(2025, 10, 10), 2, 1)),
        # Rights 28:10 at Rs 10 (premium 0) on 2024-03-22: TERP 154.1, open 157.15.
        ("TIL rights", (date(2024, 3, 21), 557.6), (date(2024, 3, 26), 157.15),
         _res(T.RIGHTS, date(2024, 3, 22), 28, 10, cash=10.0)),
        # Consolidation Re 1 -> Rs 10 on 2025-06-25; next VERTOZ bar 2025-07-11.
        ("VERTOZ consolidation", (date(2025, 6, 24), 9.17), (date(2025, 7, 11), 96.28),
         _res(T.SPLIT, date(2025, 6, 25), 1, 10)),
        # SME bonus 4:1 on 2021-10-07; HECPROJECT next traded 2021-10-19.
        ("HECPROJECT bonus", (date(2021, 10, 6), 210.0), (date(2021, 10, 19), 40.0),
         _res(T.BONUS, date(2021, 10, 7), 4, 1)),
    ],
)  # fmt: skip
def test_action_on_a_non_trading_ex_date_explains_the_next_bar(
    name: str,
    prev: tuple[date, float],
    curr: tuple[date, float],
    action: CorporateActionResolution,
) -> None:
    bars = [_bar(prev[0], prev[1], prev[1]), _bar(curr[0], curr[1], curr[1])]
    detector = GapDetector()
    assert len(detector.detect(bars, [], AT)) == 1  # without the action: flagged
    assert detector.detect(bars, [action], AT) == []


def test_unrelated_action_inside_a_long_absence_does_not_explain_the_gap() -> None:
    # GOODYEAR: no NSE bar between 2023-10-25 and 2026-04-20. A 10:1 split in that window
    # would predict an open near 127, not 800, so the gap stays flagged.
    bars = [_bar(date(2023, 10, 25), 1304.15, 1273.7), _bar(date(2026, 4, 20), 800.0, 797.45)]
    (event,) = GapDetector().detect(bars, [_res(T.SPLIT, date(2025, 1, 1), 10, 1)], AT)
    assert event.context["residual_gap_pct_after_actions"] == pytest.approx(528.1, abs=0.1)


def test_ratio_that_does_not_fit_the_gap_is_flagged_even_on_the_ex_date() -> None:
    bars = [_bar(date(2024, 1, 1), 100.0, 100.0), _bar(date(2024, 1, 2), 10.0, 10.0)]
    (event,) = GapDetector().detect(bars, [_res(T.SPLIT, date(2024, 1, 2), 2, 1)], AT)
    assert event.context["residual_gap_pct_after_actions"] == pytest.approx(-80.0)
    assert event.blocks_signal is True


def test_ex_date_on_the_previous_bar_is_outside_the_window() -> None:
    bars = [_bar(date(2024, 1, 1), 100.0, 100.0), _bar(date(2024, 1, 2), 50.0, 50.0)]
    assert len(GapDetector().detect(bars, [_res(T.SPLIT, date(2024, 1, 1), 2, 1)], AT)) == 1


def test_same_window_split_and_bonus_combine() -> None:
    # GICL 2025-10-15: split Rs 10 -> Rs 5 and bonus 1:1 the same day, 173.7 -> 46.0.
    bars = [_bar(date(2025, 10, 14), 174.95, 173.7), _bar(date(2025, 10, 15), 46.0, 46.5)]
    actions = [
        _res(T.SPLIT, date(2025, 10, 15), 10, 5),
        _res(T.BONUS, date(2025, 10, 15), 1, 1),
    ]
    assert GapDetector().detect(bars, actions, AT) == []
    assert len(GapDetector().detect(bars, actions[:1], AT)) == 1  # the split alone: -47 %


def test_demerger_without_a_derivable_factor_defers_to_its_own_event() -> None:
    # The demerger factor is the ex-date open over the prior close; when the stock did not
    # trade on the ex-date the engine raises factor_unknown, which blocks on its own.
    bars = [_bar(date(2024, 5, 21), 43.8, 43.8), _bar(date(2024, 5, 27), 20.0, 20.0)]
    assert GapDetector().detect(bars, [_res(T.DEMERGER, date(2024, 5, 22), None, None)], AT) == []
