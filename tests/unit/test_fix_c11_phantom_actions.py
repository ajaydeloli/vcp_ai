"""Fix C11 (2026-10-04): non-equity bonuses and split/bonus factors the raw prices do not show.

SIYSIL ranked #1 on 2026-10-01 although its close was below its 50-day average: NSE's
"Scheme Of Arrangement - Bonus Ncrps 4:1" (a bonus of preference shares) was read as a 4:1
equity bonus, dividing every earlier price by 5.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider
from vcp_scanner.data.quality.events import corporate_action_events
from vcp_scanner.domain.corporate_actions import (
    CorporateActionResolution,
    CorporateActionStatus,
    ExDatePrices,
    ratio_unconfirmed,
    superseded_by_unmodelled,
)
from vcp_scanner.domain.enums import CorporateActionType as T

EX = date(2026, 8, 21)
NOW = datetime(2026, 10, 4, tzinfo=UTC)


def _record(subject: str) -> dict[str, Any]:
    return {"symbol": "SIYSIL", "series": "EQ", "subject": subject, "exDate": "21-Aug-2026",
            "recDate": "22-Aug-2026", "isin": "INE076B01010", "faceVal": "2",
            "comp": "Siyaram Silk Mills Limited"}  # fmt: skip


def _res(t: T = T.BONUS, num: float | None = 4.0, den: float | None = 1.0,
         status: CorporateActionStatus = CorporateActionStatus.SINGLE_SOURCE,
         rid: str = "b") -> CorporateActionResolution:  # fmt: skip
    return CorporateActionResolution(rid, "NSE_EQ|SIYSIL", t, status, ex_date=EX,
                                     ratio_numerator=num, ratio_denominator=den)  # fmt: skip


NO_GAP = ExDatePrices(prior_close=647.55, ex_open=635.0, raw=True)  # SIYSIL 2026-08-20/21
GAP = ExDatePrices(prior_close=647.55, ex_open=130.0, raw=True)  # what a real 4:1 bonus does


def test_bonus_of_preference_shares_is_not_an_equity_bonus() -> None:
    p = NSECorporateActionProvider()
    for subject in ("Scheme Of Arrangement - Bonus Ncrps 4:1", "Bonus NCRPS 46:1",
                    "Bonus Ccps 1:1", "Bonus Preference Shares 1:1"):  # fmt: skip
        a = p._parse_nse_action(_record(subject))
        assert a is not None and a.action_type is T.UNMODELLED, subject
    plain = p._parse_nse_action(_record("Bonus 1:1"))
    assert plain is not None and plain.action_type is T.BONUS
    assert (plain.ratio_numerator, plain.ratio_denominator) == (1.0, 1.0)


def test_ratio_unconfirmed() -> None:
    assert ratio_unconfirmed(0.2, NO_GAP)  # a 4:1 bonus the prices do not show
    assert not ratio_unconfirmed(0.2, GAP)
    assert not ratio_unconfirmed(0.9, NO_GAP)  # too small to tell from a normal day
    assert not ratio_unconfirmed(0.2, None)  # no prices yet
    assert not ratio_unconfirmed(0.2, ExDatePrices(647.55, None, raw=True))  # no ex-date bar
    assert not ratio_unconfirmed(0.2, ExDatePrices(647.55, 635.0, raw=False))  # provider-adjusted
    assert ratio_unconfirmed(10.0, ExDatePrices(100.0, 101.0, raw=True))  # consolidation 1:10


def test_engine_withholds_an_unconfirmed_factor() -> None:
    engine = AdjustmentEngine()
    assert engine.compute_factors([_res()], {EX: NO_GAP}) == []
    (applied,) = engine.compute_factors([_res()], {EX: GAP})
    assert applied.price_factor == 0.2
    assert engine.compute_factors([_res()], {}) != []  # nothing to check against: applied
    manual = _res(status=CorporateActionStatus.MANUAL_OVERRIDE)
    assert engine.compute_factors([manual], {EX: NO_GAP}) != []  # a person entered it


def test_new_unmodelled_reading_supersedes_the_old_bonus_reading() -> None:
    old = _res()
    new = _res(T.UNMODELLED, None, None, rid="u")
    assert superseded_by_unmodelled(old, [old, new])
    assert not superseded_by_unmodelled(old, [old])
    assert AdjustmentEngine().compute_factors([old, new], {EX: GAP}) == []


def test_unconfirmed_ratio_raises_one_blocking_event() -> None:
    both = [_res(rid="a"), _res(num=3.0, rid="b")]  # NSE listed 4:1 and 3:1 for one record
    events = corporate_action_events("NSE_EQ|SIYSIL", both, NOW, ex_prices={EX: NO_GAP})
    assert len(events) == 1
    (e,) = events
    assert e.blocks_signal and e.context["cause"] == "ratio_unconfirmed" and e.trade_date == EX
    assert corporate_action_events("NSE_EQ|SIYSIL", [_res()], NOW, ex_prices={EX: GAP}) == []
    superseded = [_res(), _res(T.UNMODELLED, None, None, rid="u")]
    events = corporate_action_events("NSE_EQ|SIYSIL", superseded, NOW, ex_prices={EX: NO_GAP})
    blocking = [e for e in events if e.blocks_signal]
    assert blocking == []
