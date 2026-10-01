"""Share consolidations are reverse splits; capital reductions stay reported (audit 2.7b).

Real NSE records, copied from the API on 2026-10-01.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider, parse_ratio
from vcp_scanner.domain.corporate_actions import CorporateActionResolution, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType as T

_COMMON = {"bcEndDate": "-", "bcStartDate": "-", "caBroadcastDate": None, "ind": "-",
           "ndEndDate": "-", "ndStartDate": "-", "series": "EQ"}  # fmt: skip
VERTOZ = {**_COMMON, "comp": "Vertoz Limited", "exDate": "25-Jun-2025", "faceVal": "10",
          "isin": "INE188Y01015", "recDate": "25-Jun-2025", "symbol": "VERTOZ",
          "subject": ("Consolidation Of Equity Shares From Re 1 Per Share"
                      " To Rs 10 Per Share")}  # fmt: skip
MAXIND = {**_COMMON, "comp": "Max India Limited", "exDate": "26-Jul-2022", "faceVal": "10",
          "isin": "INE0CG601016", "recDate": "27-Jul-2022", "symbol": "MAXIND",
          "subject": "Capital Reduction Pursuant To Nclt Order"}  # fmt: skip
EASTSILK = {**_COMMON, "comp": "Eastern Silk Industries Limited", "exDate": "22-Nov-2024",
            "faceVal": "2", "isin": "INE962C01027", "recDate": "23-Nov-2024",
            "symbol": "EASTSILK", "subject": "Capital Reduction"}  # fmt: skip


def _provider() -> NSECorporateActionProvider:
    p = NSECorporateActionProvider()
    p.unparsed_ratios, p.unhandled_records = [], []
    return p


def test_vertoz_consolidation_is_a_reverse_split() -> None:
    p = _provider()
    action = p._parse_nse_action(VERTOZ)
    assert action is not None
    assert action.action_type is T.SPLIT
    assert (action.ratio_numerator, action.ratio_denominator) == (1.0, 10.0)
    assert action.ex_date == date(2025, 6, 25)
    assert p.unhandled_records == [] and p.unparsed_ratios == []


def test_reverse_split_multiplies_prices_and_divides_volume() -> None:
    res = CorporateActionResolution(
        "r", "NSE_EQ|VERTOZ", T.SPLIT, CorporateActionStatus.SINGLE_SOURCE,
        ex_date=date(2025, 6, 25), ratio_numerator=1.0, ratio_denominator=10.0,
    )  # fmt: skip
    assert AdjustmentEngine().single_factor(res) == (10.0, 0.1)


def test_consolidation_without_two_face_values_is_reported_unparsed() -> None:
    p = _provider()
    action = p._parse_nse_action({**VERTOZ, "subject": "Consolidation Of Equity Shares"})
    assert action is not None and action.ratio_numerator is None
    assert len(p.unparsed_ratios) == 1


@pytest.mark.parametrize("record", [MAXIND, EASTSILK], ids=["MAXIND", "EASTSILK"])
def test_capital_reduction_stays_unhandled_and_reported(record: dict[str, Any]) -> None:
    p = _provider()
    action = p._parse_nse_action(record)  # audit P1-10: kept as UNMODELLED, not dropped
    assert action is not None and action.action_type is T.UNMODELLED
    assert action.ratio_numerator is None and p.unparsed_ratios == []
    assert p.unhandled_records == [f"{record['symbol']}: {record['subject'].upper()}"]


BRITANNIA = {**_COMMON, "comp": "Britannia Industries Limited", "exDate": "25-May-2021",
             "faceVal": "1", "isin": "INE216A01014", "recDate": "27-May-2021",
             "symbol": "BRITANNIA",
             "subject": (" Scheme Of Arangement- Bonus - 1 Debenture"
                         " For 1 Equity Share Held")}  # fmt: skip


def test_bonus_of_debentures_is_not_a_share_bonus() -> None:
    # Audit P1-2a: read as a ratio-less BONUS it blocked BRITANNIA from 2021 on.
    p = _provider()
    action = p._parse_nse_action(BRITANNIA)
    assert action is not None and action.action_type is T.UNMODELLED
    assert p.unparsed_ratios == []
    assert p.unhandled_records == [
        "BRITANNIA: SCHEME OF ARANGEMENT- BONUS - 1 DEBENTURE FOR 1 EQUITY SHARE HELD"
    ]


def test_consolidation_text_parses_with_split_semantics() -> None:
    text = "CONSOLIDATION OF EQUITY SHARES FROM RE 1 PER SHARE TO RS 10 PER SHARE"
    assert parse_ratio(text, T.SPLIT) == (1.0, 10.0)
