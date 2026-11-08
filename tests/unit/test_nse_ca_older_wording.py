"""NSE corporate-action wording of 2011-2020 (history extension, 2026-11-08). Each text is a
real NSE record; the parser must read them without changing any reading it already had."""

from __future__ import annotations

import pytest

from vcp_scanner.data.providers.nse_ca import parse_ratio, rights_issue_price
from vcp_scanner.domain.enums import CorporateActionType as T


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # a split announced together with a dividend: the split's own "from ... to ..."
        ("ANNUAL GENERAL MEETING / DIVIDEND - RS 1.35/- PER SHARE / FACE VALUE SPLIT - FROM RS "
         "10/- PER SHARE TO RS 2/- PER SHARE", (10.0, 2.0)),  # MANINFRA 2014
        ("INTERIM DIV - RS 6/- PER SHARE + FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER "
         "SHARE TO RE 1/- PER SHARE", (10.0, 1.0)),  # WELSPUNLIV 2016
        # unchanged: two amounts only
        ("FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RE 1/- PER SHARE",
         (10.0, 1.0)),
    ],
)  # fmt: skip
def test_split_face_values(text: str, expected: tuple[float, float]) -> None:
    assert parse_ratio(text, T.SPLIT) == expected


def test_a_dividend_text_with_three_amounts_and_no_split_phrase_stays_unread() -> None:
    assert parse_ratio("DIVIDEND RS 2 / RS 3 / SPLIT RS 10", T.SPLIT) is None


def test_fractional_bonus_ratios() -> None:
    assert parse_ratio("BONUS 1.5:1", T.BONUS) == (1.5, 1.0)  # TOUCHWOOD 2019
    assert parse_ratio("BONUS 1:1.5", T.BONUS) == (1.0, 1.5)  # CONTI 2020 (was read 1:1)
    assert parse_ratio("BONUS 1:2", T.BONUS) == (1.0, 2.0)


@pytest.mark.parametrize(
    ("text", "face", "ratio", "price"),
    [
        ("RIGHTS - 2:3@ PAR RS 10 PER SHARE", 10, (2.0, 3.0), 10.0),  # ALOKTEXT 2013
        ("RIGHTS : 24:10 AT PAR", 10, (24.0, 10.0), 10.0),  # ASHIMASYN 2015
        ("RIGHTS 1:2@PREM RS 40", 10, (1.0, 2.0), 50.0),  # CREST 2016
        ("RIGHTS 87:38 @ PREMIUM OF RS 2.50 PER SHARE", 10, (87.0, 38.0), 12.5),  # IDEA 2019
        ("RIGHTS 1:14 @ PREMIUM RS 530/-", 5, (1.0, 14.0), 535.0),  # unchanged (BHARTIARTL)
        ("RIGHTS 7:10 @ PRM RS 102/-", 10, (7.0, 10.0), 112.0),  # unchanged
    ],
)
def test_rights_wording(text: str, face: int, ratio: tuple, price: float) -> None:
    assert parse_ratio(text, T.RIGHTS) == ratio
    assert rights_issue_price(text, face) == price


def test_rights_without_a_price_stay_unknown() -> None:
    assert rights_issue_price("RIGHTS 2:7", 10) is None  # JMCPROJECT 2016: no price given
    assert rights_issue_price("RIGHTS 1:2 @ PARTLY PAID", 10) is None  # "PAR" must be a word


def _record(subject: str, symbol: str = "INDOCO", isin: str = "INE873D01024") -> dict:
    return {"bcEndDate": "-", "bcStartDate": "-", "ind": "-", "ndEndDate": "-",
            "ndStartDate": "-", "series": "EQ", "comp": "X", "exDate": "17-May-2012",
            "faceVal": "10", "isin": isin, "recDate": "-", "symbol": symbol,
            "subject": subject}  # fmt: skip


def test_a_bonus_and_split_in_one_record_give_both_actions() -> None:
    from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider

    p = NSECorporateActionProvider()
    p.unparsed_ratios, p.unhandled_records = [], []
    indoco = "Bonus 1:2 And Face Value Split From Rs.10 To Rs.2"
    split, bonus = p._parse_nse_actions(_record(indoco))
    assert (split.action_type, split.ratio_numerator, split.ratio_denominator) == (T.SPLIT, 10, 2)
    assert (bonus.action_type, bonus.ratio_numerator, bonus.ratio_denominator) == (T.BONUS, 1, 2)
    assert bonus.ex_date == split.ex_date and bonus.isin == split.isin
    assert bonus.corporate_action_id != split.corporate_action_id
    # the same record always gives the same two IDs
    again = p._parse_nse_actions(_record(indoco))
    assert [a.corporate_action_id for a in again] == [split.corporate_action_id,
                                                      bonus.corporate_action_id]  # fmt: skip
    # a split alone, and a bonus of debentures next to a split, stay one action
    [only] = p._parse_nse_actions(_record("Face Value Split From Rs 10/- To Rs 2/-"))
    assert only.action_type is T.SPLIT
    deb = p._parse_nse_actions(_record("Split Rs 10 To Rs 2 / Bonus Debentures 1:1"))
    assert [a.action_type for a in deb] != [T.SPLIT, T.BONUS]


def test_nse_shorthand_fv_splt_is_a_split() -> None:
    from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider

    p = NSECorporateActionProvider()
    p.unparsed_ratios, p.unhandled_records = [], []
    [a] = p._parse_nse_actions(_record(" Fv Splt Frm Rs 10 To Re 1", "JSWSTEEL", "INE019A01020"))
    assert (a.action_type, a.ratio_numerator, a.ratio_denominator) == (T.SPLIT, 10, 1)


def test_all_records_ignores_the_instrument_filter_only() -> None:
    from vcp_scanner.cli_pipeline import _AllRecords

    class Stub:
        unparsed_ratios = ["x"]

        def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN202
            self.seen = instruments
            return ["a"]

    inner = Stub()
    wrapped = _AllRecords(inner)
    assert wrapped.get_actions(1, 2, ["only-these"]) == ["a"] and inner.seen is None
    assert wrapped.unparsed_ratios == ["x"]  # everything else is the wrapped provider's
