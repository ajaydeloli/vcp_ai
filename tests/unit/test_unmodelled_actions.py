"""Audit P1-10: price-affecting NSE actions the scanner does not model become warning events.

Records are real NSE API records (2026-10-01).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider
from vcp_scanner.data.quality.events import unmodelled_action_events
from vcp_scanner.domain.corporate_actions import CorporateActionResolution, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType as T
from vcp_scanner.domain.enums import DataQualityFlag

AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
_COMMON = {"bcEndDate": "-", "bcStartDate": "-", "caBroadcastDate": None, "ind": "-",
           "ndEndDate": "-", "ndStartDate": "-", "series": "EQ", "faceVal": "10"}  # fmt: skip
EASTSILK = {**_COMMON, "comp": "Eastern Silk Industries Limited", "exDate": "22-Nov-2024",
            "isin": "INE962C01027", "recDate": "23-Nov-2024", "symbol": "EASTSILK",
            "subject": "Capital Reduction"}  # fmt: skip
QUINT = {**_COMMON, "comp": "Quint Digital Limited", "exDate": "25-Aug-2026",
         "isin": "INE641R01017", "recDate": "25-Aug-2026", "symbol": "QUINT",
         "subject": "Rights - 7 Ccps And 7 Warrants:40"}  # fmt: skip
EQUITY_RIGHTS = {**QUINT, "subject": "Rights 7:10 @ Premium Rs 102/-"}


def _parse(record: dict[str, object]):  # noqa: ANN202
    p = NSECorporateActionProvider()
    p.unparsed_ratios, p.unhandled_records = [], []
    return p._parse_nse_action(record), p


def test_capital_reduction_is_kept_as_unmodelled_with_its_text() -> None:
    action, p = _parse(EASTSILK)
    assert action is not None and action.action_type is T.UNMODELLED
    assert action.ex_date == date(2024, 11, 22)
    assert action.source_record_id == "CAPITAL REDUCTION"
    assert p.unhandled_records == ["EASTSILK: CAPITAL REDUCTION"]


def test_rights_in_ccps_and_warrants_are_not_equity_rights() -> None:
    action, p = _parse(QUINT)
    assert action is not None and action.action_type is T.UNMODELLED
    assert p.unparsed_ratios == []  # no longer a ratio-less RIGHTS (which blocked QUINT)
    equity, _ = _parse(EQUITY_RIGHTS)
    assert equity is not None and equity.action_type is T.RIGHTS


def _res(kind: T, ex: date, status: CorporateActionStatus = CorporateActionStatus.SINGLE_SOURCE,
         num: float | None = None) -> CorporateActionResolution:  # fmt: skip
    return CorporateActionResolution(
        f"r-{kind}-{ex}", "I", kind, status, ex_date=ex,
        ratio_numerator=num, ratio_denominator=1.0 if num else None,
    )  # fmt: skip


def test_warning_event_until_a_manual_action_covers_the_date() -> None:
    ex = date(2024, 11, 22)
    (event,) = unmodelled_action_events("I", [_res(T.UNMODELLED, ex)], AT)
    assert event.flag is DataQualityFlag.CORPORATE_ACTION_UNMODELLED
    assert event.blocks_signal is False and event.trade_date == ex
    manual = _res(T.SPLIT, ex, CorporateActionStatus.MANUAL_OVERRIDE, num=10.0)
    assert unmodelled_action_events("I", [_res(T.UNMODELLED, ex), manual], AT) == []
    assert unmodelled_action_events("I", [_res(T.SPLIT, ex)], AT) == []
