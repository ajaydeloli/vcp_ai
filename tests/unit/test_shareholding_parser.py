"""Shareholding-pattern parser (F8) on real NSE filings saved 2026-10-13, one per layout.

Known answers are the filings' own numbers (NSE's listing shows the same promoter and public
totals: RELIANCE 30 Jun 2026 promoter 50.48 / public 49.52)."""

from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

import pytest

from vcp_scanner.shareholding.parser import (
    FORMAT_NEW,
    FORMAT_OLD,
    FORMAT_SPLIT,
    ShareholdingParseError,
    parse_shareholding,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "shareholding"


def load(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.xml.gz").read_bytes())


def _parts(s) -> float:  # type: ignore[no-untyped-def]
    return s.dii_pct + s.fii_pct + (s.fdi_pct or 0) + s.government_pct + s.non_institutions_pct


def test_new_layout_reliance_june_2026() -> None:
    s = parse_shareholding(load("reliance_2026q1fy27_new"))
    assert (s.layout, s.symbol, s.report_date) == (FORMAT_NEW, "RELIANCE", date(2026, 6, 30))
    assert s.promoter_pct == pytest.approx(50.48) and s.public_pct == pytest.approx(49.52)
    assert s.fii_pct == pytest.approx(16.52 + 0.54)  # FPI categories I and II
    assert s.fdi_pct == pytest.approx(0.13)
    assert s.dii_pct == pytest.approx(21.19)
    assert (s.mf_pct, s.insurance_pct) == (pytest.approx(10.11), pytest.approx(9.2))
    assert s.promoter_pledged_pct == 0.0  # the filing says no promoter shares are pledged
    assert s.shareholders == 4_651_863
    assert _parts(s) == pytest.approx(s.public_pct, abs=0.05)


def test_new_layout_with_a_pledge_jswsteel_june_2026() -> None:
    s = parse_shareholding(load("jswsteel_2026q1fy27_new"))
    assert s.layout == FORMAT_NEW
    assert s.promoter_pct == pytest.approx(44.29)
    # 125,650,440 of the promoters' 1,083,203,750 shares: 11.6 % of promoter shares
    assert s.promoter_pledged_pct == pytest.approx(11.6)
    assert s.fdi_pct == pytest.approx(15.0)  # JFE Steel's strategic stake: not counted as FII
    assert s.fii_pct == pytest.approx(10.91)
    assert _parts(s) == pytest.approx(s.public_pct, abs=0.05)


def test_split_layout_jswsteel_march_2025() -> None:
    """Old context names, the domestic/foreign split, FPI contexts spelt 'Catergory'."""
    s = parse_shareholding(load("jswsteel_2025q4fy25_split"))
    assert (s.layout, s.report_date) == (FORMAT_SPLIT, date(2025, 3, 31))
    assert s.promoter_pledged_pct == pytest.approx(13.36)  # pledged or otherwise encumbered
    assert s.fii_pct == pytest.approx(10.77) and s.dii_pct == pytest.approx(10.52)
    assert _parts(s) == pytest.approx(s.public_pct, abs=0.05)


def test_old_layout_reliance_september_2021() -> None:
    """One Institutions group; domestic = institutions less FPI (and foreign venture capital)."""
    s = parse_shareholding(load("reliance_2021q2fy22_old"))
    assert (s.layout, s.report_date) == (FORMAT_OLD, date(2021, 9, 30))
    assert s.promoter_pct == pytest.approx(50.61) and s.public_pct == pytest.approx(49.39)
    assert s.fii_pct == pytest.approx(25.39) and s.dii_pct == pytest.approx(38.61 - 25.39)
    assert s.fdi_pct is None
    assert s.shareholders == 3_043_511
    assert _parts(s) == pytest.approx(s.public_pct, abs=0.05)


@pytest.mark.parametrize(
    "name", ["reliance_2026q1fy27_new", "jswsteel_2026q1fy27_new", "jswsteel_2025q4fy25_split",
             "reliance_2021q2fy22_old"],
)  # fmt: skip
def test_every_layout_sums_to_100(name: str) -> None:
    s = parse_shareholding(load(name))
    assert s.promoter_pct + s.public_pct + s.other_pct == pytest.approx(100.0, abs=0.05)


def test_not_xml_and_missing_totals_are_refused() -> None:
    with pytest.raises(ShareholdingParseError, match="not XML"):
        parse_shareholding(b"<html>Access denied")
    with pytest.raises(ShareholdingParseError, match="no promoter"):
        parse_shareholding(b"<xbrl></xbrl>")


def test_a_filing_that_does_not_add_up_is_refused() -> None:
    tag = b'ShareholdingOfPromoterAndPromoterGroup_ContextI" decimals="INF" unitRef="pure">'
    original = load("reliance_2026q1fy27_new")
    data = original.replace(tag + b"0.5048", tag + b"0.6048")
    assert data != original
    with pytest.raises(ShareholdingParseError, match="is not 100"):
        parse_shareholding(data)
