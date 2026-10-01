"""NSE SME (Emerge) corporate actions come from a second feed, ``index=sme`` (audit 2.7).

The records below are real, copied from NSE's API on 2026-10-01.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.errors import ProviderError

KSOLVES_BONUS = {
    "bcEndDate": "-", "bcStartDate": "-", "caBroadcastDate": "17-Dec-2021",
    "comp": "Ksolves India Limited", "exDate": "04-Jun-2021", "faceVal": "10", "ind": None,
    "isin": "341033", "ndEndDate": "-", "ndStartDate": "-", "recDate": "07-Jun-2021",
    "series": "SM", "subject": " BONUS 3:1/DIVIDEND - RS 30 PER SHARE", "symbol": "KSOLVES",
}  # fmt: skip
VCL_SPLIT = {
    "bcEndDate": "-", "bcStartDate": "-", "caBroadcastDate": "21-Mar-2022",
    "comp": "Vaxtex Cotfab Limited", "exDate": "28-Mar-2022", "faceVal": "2", "ind": None,
    "isin": "363683", "ndEndDate": "-", "ndStartDate": "-", "recDate": "29-Mar-2022",
    "series": "SM",
    "subject": "FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RS 2/- PER SHARE",
    "symbol": "VCL",
}  # fmt: skip
DOLPHIN_SPLIT = {
    "bcEndDate": "-", "bcStartDate": "-", "caBroadcastDate": None,
    "comp": "Dolphin Offshore Enterprises (India) Limited", "exDate": "25-Jan-2024",
    "faceVal": "1", "ind": "-", "isin": "INE920A01029", "ndEndDate": "-", "ndStartDate": "-",
    "recDate": "25-Jan-2024", "series": "EQ",
    "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share",
    "symbol": "DOLPHIN",
}  # fmt: skip


def _resp(status: int = 200, json_data: object = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = "" if status == 200 else "boom"
    r.json.return_value = json_data
    return r


def _provider(by_index: dict[str, MagicMock]) -> tuple[NSECorporateActionProvider, MagicMock]:
    p = NSECorporateActionProvider()
    session = MagicMock()

    def get(url: str, params: dict[str, str] | None = None, timeout: int = 0) -> MagicMock:
        if params is None:  # homepage cookie warm-up
            return _resp(200)
        return by_index[params["index"]]

    session.get.side_effect = get
    p._session = session
    return p, session


def test_both_feeds_are_fetched_and_merged() -> None:
    p, session = _provider(
        {"equities": _resp(200, [DOLPHIN_SPLIT]), "sme": _resp(200, [KSOLVES_BONUS, VCL_SPLIT])}
    )
    actions = p.get_actions(date(2021, 1, 1), date(2024, 12, 31))

    calls = session.get.call_args_list
    asked = [c.kwargs["params"]["index"] for c in calls if c.kwargs.get("params")]
    assert asked == ["equities", "sme"]
    by_symbol = {a.instrument_id: a for a in actions}
    assert set(by_symbol) == {"NSE_EQ|DOLPHIN", "NSE_EQ|KSOLVES", "NSE_EQ|VCL"}

    ksolves = by_symbol["NSE_EQ|KSOLVES"]
    assert ksolves.action_type is CorporateActionType.BONUS
    assert (ksolves.ratio_numerator, ksolves.ratio_denominator) == (3.0, 1.0)
    assert ksolves.ex_date == date(2021, 6, 4)

    vcl = by_symbol["NSE_EQ|VCL"]
    assert vcl.action_type is CorporateActionType.SPLIT
    assert (vcl.ratio_numerator, vcl.ratio_denominator) == (10.0, 2.0)
    assert p.unparsed_ratios == []


def test_sme_internal_number_is_not_taken_for_an_isin() -> None:
    p, _ = _provider({"equities": _resp(200, [DOLPHIN_SPLIT]), "sme": _resp(200, [KSOLVES_BONUS])})
    by_symbol = {a.instrument_id: a for a in p.get_actions(date(2021, 1, 1), date(2024, 12, 31))}
    assert by_symbol["NSE_EQ|KSOLVES"].isin is None  # "341033" is not an ISIN
    assert by_symbol["NSE_EQ|DOLPHIN"].isin == "INE920A01029"


def test_a_record_in_both_feeds_is_kept_once() -> None:
    p, _ = _provider({"equities": _resp(200, [DOLPHIN_SPLIT]), "sme": _resp(200, [DOLPHIN_SPLIT])})
    assert len(p.get_actions(date(2024, 1, 1), date(2024, 1, 31))) == 1


@pytest.mark.parametrize("failing", ["equities", "sme"])
def test_failure_of_either_feed_raises(failing: str) -> None:
    ok = _resp(200, [])
    p, _ = _provider({"equities": ok, "sme": ok, failing: _resp(500)})
    with pytest.raises(ProviderError, match=f"index={failing}"):
        p.get_actions(date(2024, 1, 1), date(2024, 1, 31))


def test_a_non_list_answer_raises_instead_of_reading_as_empty() -> None:
    p, _ = _provider({"equities": _resp(200, []), "sme": _resp(200, {"error": "x"})})
    with pytest.raises(ProviderError, match="expected a list"):
        p.get_actions(date(2024, 1, 1), date(2024, 1, 31))
