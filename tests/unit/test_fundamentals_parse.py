"""Fundamentals F2: XBRL reading and snapshots, with real saved NSE filings as known answers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.providers.nse_filings import parse_old_row
from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
    DuckDBFundamentalRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.fundamentals.base import FilingListing, FilingRef
from vcp_scanner.fundamentals.fetch import fetch_filings
from vcp_scanner.fundamentals.snapshots import parse_filings
from vcp_scanner.fundamentals.xbrl import FilingParseError, parse_xbrl

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "fundamentals"
NOW = datetime(2026, 10, 10, tzinfo=UTC)
EPS_TAG = "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"


def load(name: str) -> bytes:
    return (FIX / f"{name}.xml").read_bytes()


# --- reading real filings (known answers from the filings themselves) -----------------------


def test_reliance_q3_fy25_consolidated() -> None:
    p = parse_xbrl(load("reliance_q3fy25_con"))
    assert (p.basis, p.currency, p.period_type) == ("CONSOLIDATED", "INR", "QUARTER")
    assert (p.period.start, p.period.end) == (date(2024, 10, 1), date(2024, 12, 31))
    assert p.period.items["revenue"] == 2438650000000.0  # Rs 2,43,865 crore
    assert p.period.items["eps"] == 13.70
    assert p.period.items["profit_owners"] == 185400000000.0
    assert p.ytd is not None and (p.ytd.start, p.ytd.items["eps"]) == (date(2024, 4, 1), 37.13)
    assert not p.is_financial


def test_standalone_is_a_different_basis_with_different_numbers() -> None:
    p = parse_xbrl(load("reliance_q3fy25_non"))
    assert p.basis == "STANDALONE" and p.period.items["eps"] == 6.44


def test_split_case_eps_is_kept_as_filed() -> None:
    """Reliance's 1:1 bonus (Oct 2024) halved EPS: the pre-bonus quarter reads 25.52 against
    13.70 a year later. F2 stores EPS as filed; F3 applies the corporate-action factor."""
    before = parse_xbrl(load("reliance_q3fy24_con")).period.items["eps"]
    after = parse_xbrl(load("reliance_q3fy25_con")).period.items["eps"]
    assert (before, after) == (25.52, 13.70)


def test_new_feed_filing_reads_with_the_same_parser() -> None:
    p = parse_xbrl(load("reliance_q1fy27_new_feed_con"))
    assert p.basis == "CONSOLIDATED" and p.ytd is None  # a first quarter has no year to date
    assert (p.period.start, p.period.end) == (date(2026, 4, 1), date(2026, 6, 30))
    assert p.period.items["revenue"] == 3118500000000.0 and p.period.items["eps"] == 15.48
    assert p.instant["debt_equity_ratio"] == 0.004


def test_bank_uses_total_income_and_its_own_tag_names() -> None:
    p = parse_xbrl(load("hdfcbank_q3fy25_con"))
    assert p.is_financial
    assert p.period.items["revenue"] == 1121939400000.0  # total income
    assert p.period.items["eps"] == 23.11 and p.period.items["net_profit"] == 183401100000.0
    assert "finance_costs" not in p.period.items and "depreciation" not in p.period.items


def test_annual_filing_carries_balance_sheet_and_year_figures() -> None:
    p = parse_xbrl(load("reliance_fy24_annual_con"))
    assert p.ytd is not None and p.ytd.days == 366
    assert p.ytd.items["eps"] == 102.9
    assert p.instant["equity"] == 7934810000000.0 and p.instant["borrowings"] == 3246220000000.0


@pytest.mark.parametrize(
    "data",
    [
        b"not xml at all",
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>',
        b"<xbrl></xbrl>",
    ],
)
def test_unreadable_files_raise(data: bytes) -> None:
    with pytest.raises(FilingParseError):
        parse_xbrl(data)


# --- snapshots ------------------------------------------------------------------------------


class MapProvider:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def list_filings(self, **_: Any) -> FilingListing:
        raise NotImplementedError

    def download(self, url: str) -> bytes:
        return self.files[url]


def old_ref(seq: str, to: str, period: str, basis: str, broadcast: str) -> FilingRef:
    ref = parse_old_row({
        "symbol": "RELIANCE", "period": period, "seqNumber": seq, "toDate": to,
        "broadCastDate": broadcast, "consolidated": basis, "audited": "Un-Audited",
        "xbrl": f"https://h/{seq}.xml", "reInd": "N", "oldNewFlag": "N",
    })  # fmt: skip
    assert ref is not None
    return ref


@pytest.fixture
def env(tmp_path: Path) -> tuple[DuckDBStore, DuckDBFundamentalRepository, Path]:
    store = DuckDBStore(":memory:")
    store.migrate()
    return store, DuckDBFundamentalRepository(store), tmp_path


def store_filings(
    repo: DuckDBFundamentalRepository, root: Path, items: list[tuple[FilingRef, bytes]]
) -> None:
    files = {ref.url: data for ref, data in items}
    from vcp_scanner.fundamentals.base import FilingListing as L

    class P(MapProvider):
        def list_filings(self, **_: Any) -> L:
            return L([ref for ref, _ in items])

    fetch_filings(P(files), repo, root, {"RELIANCE": "INS1"}, clock=lambda: NOW)


def test_snapshots_from_real_filings_and_idempotence(env: Any) -> None:
    store, repo, root = env
    con = old_ref("1", "31-Dec-2024", "Quarterly", "Consolidated", "16-Jan-2025 20:15:20")
    non = old_ref("2", "31-Dec-2024", "Quarterly", "Non-Consolidated", "16-Jan-2025 20:20:21")
    annual = old_ref("3", "31-Mar-2024", "Annual", "Consolidated", "22-Apr-2024 19:47:12")
    store_filings(repo, root, [(con, load("reliance_q3fy25_con")),
                               (non, load("reliance_q3fy25_non")),
                               (annual, load("reliance_fy24_annual_con"))])  # fmt: skip
    report = parse_filings(repo, root)
    assert (report.parsed, report.errors, report.invalid) == (3, 0, 0)
    rows = store.conn.execute(
        "SELECT period_end, period_type, statement_basis, revision_number, data_status,"
        " available_at FROM fundamental_snapshots ORDER BY period_end, statement_basis"
    ).fetchall()
    assert [r[:5] for r in rows] == [
        (date(2024, 3, 31), "ANNUAL", "CONSOLIDATED", 0, "OK"),
        (date(2024, 12, 31), "QUARTER", "CONSOLIDATED", 0, "OK"),
        (date(2024, 12, 31), "QUARTER", "STANDALONE", 0, "OK"),
    ]
    assert rows[1][5].astimezone(IST).isoformat() == "2025-01-16T20:15:20+05:30"
    eps = store.conn.execute(
        "SELECT scope, value FROM fundamental_facts f JOIN fundamental_snapshots s USING"
        " (fundamental_snapshot_id) WHERE item = 'eps' AND s.period_type = 'ANNUAL'"
    ).fetchall()
    assert eps == [("ANNUAL", 102.9)]  # the year, not the last quarter
    assert parse_filings(repo, root).parsed == 0  # nothing to do the second time


def test_restatement_supersedes_the_earlier_revision(env: Any) -> None:
    store, repo, root = env
    first = old_ref("1", "31-Dec-2024", "Quarterly", "Consolidated", "16-Jan-2025 20:15:20")
    second = old_ref("9", "31-Dec-2024", "Quarterly", "Consolidated", "20-Jan-2025 10:00:00")
    store_filings(repo, root, [(first, load("reliance_q3fy25_con")),
                               (second, load("reliance_q3fy25_con"))])  # fmt: skip
    assert parse_filings(repo, root).parsed == 2
    rows = store.conn.execute(
        "SELECT revision_number, superseded_by_snapshot_id IS NOT NULL FROM fundamental_snapshots"
        " ORDER BY revision_number"
    ).fetchall()
    assert rows == [(0, True), (1, False)]


def test_damaged_cache_and_garbage_are_parse_errors_not_crashes(env: Any) -> None:
    store, repo, root = env
    good = old_ref("1", "31-Dec-2024", "Quarterly", "Consolidated", "16-Jan-2025 20:15:20")
    bad = old_ref("2", "31-Dec-2024", "Quarterly", "Non-Consolidated", "16-Jan-2025 20:20:21")
    store_filings(repo, root, [(good, load("reliance_q3fy25_con")), (bad, b"garbage")])
    (root / "NSE_FINANCIAL_RESULTS" / "RELIANCE" / "1.xml").write_bytes(b"tampered")
    report = parse_filings(repo, root)
    assert (report.parsed, report.errors) == (0, 2)
    assert {
        r[0] for r in store.conn.execute("SELECT status FROM fundamental_filings").fetchall()
    } == {"PARSE_ERROR"}
    assert parse_filings(repo, root).errors == 0  # not retried until reset


def synthetic(
    start: str, end: str, ytd_start: str, quarter: dict[str, str], ytd: dict[str, str]
) -> bytes:
    def fact(name: str, ctx: str, value: str) -> str:
        return f'<f:{name} contextRef="{ctx}" unitRef="INR">{value}</f:{name}>'

    parts = [
        fact("DateOfStartOfReportingPeriod", "OneD", start),
        fact("DateOfEndOfReportingPeriod", "OneD", end),
        fact("DateOfStartOfReportingPeriod", "FourD", ytd_start),
        fact("DateOfEndOfReportingPeriod", "FourD", end),
        fact("NatureOfReportStandaloneConsolidated", "OneD", "Consolidated"),
        fact("DescriptionOfPresentationCurrency", "OneD", "INR"),
        *(fact(k, "OneD", v) for k, v in quarter.items()),
        *(fact(k, "FourD", v) for k, v in ytd.items()),
    ]
    head = '<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:f="urn:f">'
    return (head + "".join(parts) + "</x:xbrl>").encode()


def test_quarter_derived_from_year_to_date_is_marked_estimated(env: Any) -> None:
    store, repo, root = env
    # Q1 has no quarter column: year to date (Apr-Jun) is the quarter.
    q1 = synthetic("2024-04-01", "2024-06-30", "2024-04-01", {},
                   {"RevenueFromOperations": "100", EPS_TAG: "4"})  # fmt: skip
    q2 = synthetic("2024-07-01", "2024-09-30", "2024-04-01", {},
                   {"RevenueFromOperations": "250", EPS_TAG: "11"})  # fmt: skip
    r1 = old_ref("1", "30-Jun-2024", "Quarterly", "Consolidated", "20-Jul-2024 18:00:00")
    r2 = old_ref("2", "30-Sep-2024", "Quarterly", "Consolidated", "20-Oct-2024 18:00:00")
    store_filings(repo, root, [(r2, q2), (r1, q1)])  # arrives in the wrong order on purpose
    report = parse_filings(repo, root)
    assert (report.parsed, report.estimated, report.errors) == (2, 2, 0)
    got = dict(store.conn.execute(
        "SELECT s.period_end::VARCHAR || ' ' || f.item, f.value FROM fundamental_facts f"
        " JOIN fundamental_snapshots s USING (fundamental_snapshot_id)"
        " WHERE f.scope = 'QUARTER'"
    ).fetchall())  # fmt: skip
    assert got["2024-06-30 revenue"] == 100 and got["2024-09-30 revenue"] == 150
    assert got["2024-09-30 eps"] == 7
    assert store.conn.execute(
        "SELECT DISTINCT data_status FROM fundamental_snapshots"
    ).fetchall() == [("ESTIMATED",)]


def test_failed_sanity_check_is_stored_as_invalid(env: Any) -> None:
    store, repo, root = env
    bad = synthetic("2024-10-01", "2024-12-31", "2024-04-01",
                    {"RevenueFromOperations": "-5"}, {})  # fmt: skip
    ref = old_ref("1", "31-Dec-2024", "Quarterly", "Consolidated", "16-Jan-2025 20:15:20")
    store_filings(repo, root, [(ref, bad)])
    report = parse_filings(repo, root)
    assert (report.parsed, report.invalid) == (1, 1)
    assert store.conn.execute("SELECT data_status FROM fundamental_snapshots").fetchall() == [
        ("INVALID",)
    ]
