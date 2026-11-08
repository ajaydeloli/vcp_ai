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
