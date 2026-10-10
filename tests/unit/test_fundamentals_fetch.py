"""Fundamentals F1: NSE filing listing, raw cache, manifest, revisions, freeze rule."""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner.data.providers.nse_filings import (
    NseFilingProvider,
    parse_new_row,
    parse_old_row,
)
from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
    DuckDBFundamentalRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.fundamentals.base import FilingListing, FilingRef
from vcp_scanner.fundamentals.fetch import fetch_filings

OLD_ROW = {
    "symbol": "RELIANCE", "isin": "INE002A01018", "period": "Quarterly", "seqNumber": "1189823",
    "toDate": "31-Dec-2024", "broadCastDate": "16-Jan-2025 20:20:21",
    "consolidated": "Non-Consolidated", "audited": "Un-Audited", "reInd": "N",
    "oldNewFlag": "N", "relatingTo": "Third Quarter", "bank": "N",
    "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/a.xml",
}  # fmt: skip
NEW_ROW = {
    "symbol": "RELIANCE", "seq_Id": "175608", "type": "Integrated Filing- Financials",
    "qe_Date": "30-JUN-2026", "broadcast_Date": "17-Jul-2026 19:50:03",
    "consolidated": "Consolidated", "audited": "Un-Audited", "type_Sub": "Original",
    "revised_Date": None, "revision_Remark": None,
    "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/b.xml",
}  # fmt: skip
GOVERNANCE = {**NEW_ROW, "seq_Id": "179404", "type": "Integrated Filing- Governance"}
NOW = datetime(2026, 10, 5, tzinfo=UTC)


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, content: bytes = b"") -> None:
        self.status_code = status
        self._body = body
        self.content = content
        self.url = "u"

    def json(self) -> Any:
        return self._body


class FakeSession:
    def __init__(self, routes: dict[str, list[FakeResponse]]) -> None:
        self.headers: dict[str, str] = {}
        self.routes = routes
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def get(self, url: str, params: dict[str, str] | None = None, timeout: float = 0) -> Any:
        self.calls.append((url, params))
        for key, queue in self.routes.items():
            if url.endswith(key):
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeResponse()


def _provider(routes: dict[str, list[FakeResponse]]) -> tuple[NseFilingProvider, FakeSession]:
    session = FakeSession(routes)
    return NseFilingProvider(session=session, sleep=lambda _s: None), session  # type: ignore[arg-type]


def test_parse_old_row_fields() -> None:
    ref = parse_old_row(OLD_ROW)
    assert ref is not None
    assert (ref.period_end, ref.period_type, ref.statement_basis) == (
        date(2024, 12, 31),
        "QUARTER",
        "STANDALONE",
    )
    assert ref.audited is False
    assert ref.broadcast_at.utcoffset().total_seconds() == 5.5 * 3600  # type: ignore[union-attr]
    assert ref.filing_id == "NSE_FINANCIAL_RESULTS:1189823"
    assert parse_old_row({**OLD_ROW, "xbrl": None}) is None
    assert (
        parse_old_row({**OLD_ROW, "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/-"})
        is None
    )  # pre-XBRL filing


def test_parse_new_row_keeps_only_financials() -> None:
    ref = parse_new_row(NEW_ROW)
    assert ref is not None
    assert (ref.period_end, ref.statement_basis, ref.period_type) == (
        date(2026, 6, 30),
        "CONSOLIDATED",
        None,
    )
    assert parse_new_row(GOVERNANCE) is None


def test_listing_merges_feeds_and_flags_the_row_cap() -> None:
    full = [{**GOVERNANCE, "seq_Id": str(i)} for i in range(19)] + [NEW_ROW]
    provider, session = _provider({
        "corporates-financial-results": [FakeResponse(body=[OLD_ROW])],
        "integrated-filing-results": [FakeResponse(body=full)],
    })  # fmt: skip
    listing = provider.list_filings(symbol="RELIANCE", start=date(2024, 1, 1), end=date(2026, 9, 1))
    assert listing.truncated is True  # 20 rows came back
    assert {r.source_feed for r in listing.filings} == {
        "NSE_FINANCIAL_RESULTS",
        "NSE_INTEGRATED_FILING",
    }
    assert session.calls[1][1]["from_date"] == "01-01-2024"  # type: ignore[index]


def test_retry_after_403_then_ok_and_gives_up_after_limit() -> None:
    provider, _ = _provider({"x.xml": [FakeResponse(403), FakeResponse(200, content=b"<a/>")]})
    assert provider.download("https://h/x.xml") == b"<a/>"
    provider, _ = _provider({"x.xml": [FakeResponse(403)]})
    with pytest.raises(ProviderError):
        provider.download("https://h/x.xml")
    provider, session = _provider({"x.xml": [FakeResponse(404)]})
    with pytest.raises(ProviderError):
        provider.download("https://h/x.xml")
    assert sum(1 for u, _ in session.calls if u.endswith("x.xml")) == 1  # 404 is not retried


class StubProvider:
    def __init__(self, refs: list[FilingRef], payload: bytes = b"<xbrl/>") -> None:
        self.refs = refs
        self.payload = payload
        self.downloads: list[str] = []
        self.fail = False

    def list_filings(self, **_: Any) -> FilingListing:
        return FilingListing(self.refs)

    def download(self, url: str) -> bytes:
        self.downloads.append(url)
        if self.fail:
            raise ProviderError("down")
        return self.payload


@pytest.fixture
def store() -> DuckDBStore:
    s = DuckDBStore(":memory:")
    s.migrate()
    return s


def test_fetch_end_to_end_refetch_does_nothing_and_errors_retry(
    store: DuckDBStore, tmp_path: Path
) -> None:
    repo = DuckDBFundamentalRepository(store)
    ref = parse_old_row(OLD_ROW)
    assert ref is not None
    stub = StubProvider([ref])
    ids = {"RELIANCE": "INS1"}
    stub.fail = True
    r0 = fetch_filings(stub, repo, tmp_path, ids, clock=lambda: NOW)
    assert (r0.errors, r0.fetched) == (1, 0)
    assert repo.get(ref.filing_id).status == "FETCH_ERROR"  # type: ignore[union-attr]
    stub.fail = False
    r1 = fetch_filings(stub, repo, tmp_path, ids, clock=lambda: NOW)
    assert (r1.fetched, r1.errors) == (1, 0)
    row = repo.get(ref.filing_id)
    assert row is not None and row.status == "OK"
    assert row.sha256 == hashlib.sha256(b"<xbrl/>").hexdigest()
    assert (tmp_path / str(row.cache_path)).read_bytes() == b"<xbrl/>"
    before = len(stub.downloads)
    r2 = fetch_filings(stub, repo, tmp_path, ids, clock=lambda: NOW)
    assert (r2.already_stored, r2.fetched) == (1, 0)
    assert len(stub.downloads) == before  # nothing fetched again
    (tmp_path / str(row.cache_path)).write_bytes(b"tampered")  # damaged cache is fetched again
    assert fetch_filings(stub, repo, tmp_path, ids, clock=lambda: NOW).fetched == 1


def test_unknown_symbol_is_skipped_and_listing_error_is_a_warning(
    store: DuckDBStore, tmp_path: Path
) -> None:
    repo = DuckDBFundamentalRepository(store)
    ref = parse_old_row(OLD_ROW)
    assert ref is not None
    r = fetch_filings(StubProvider([ref]), repo, tmp_path, {}, clock=lambda: NOW)
    assert (r.skipped_no_instrument, r.fetched) == (1, 0)

    class Broken(StubProvider):
        def list_filings(self, **_: Any) -> FilingListing:
            raise ProviderError("blocked")

    r = fetch_filings(Broken([]), repo, tmp_path, {"RELIANCE": "I"}, clock=lambda: NOW)
    assert r.errors == 1 and r.messages


def test_restatement_becomes_revision_one(store: DuckDBStore, tmp_path: Path) -> None:
    repo = DuckDBFundamentalRepository(store)
    first = parse_old_row(OLD_ROW)
    second = parse_old_row({**OLD_ROW, "seqNumber": "1189999",
                            "broadCastDate": "20-Jan-2025 10:00:00"})  # fmt: skip
    assert first and second
    ids = {"RELIANCE": "INS1"}
    fetch_filings(StubProvider([second, first]), repo, tmp_path, ids, clock=lambda: NOW)
    rows = store.conn.execute(
        "SELECT source_record_id, revision_number FROM fundamental_filings ORDER BY 2"
    ).fetchall()
    assert rows == [("1189823", 0), ("1189999", 1)]


def test_fundamentals_package_is_not_imported_by_scan_code() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "vcp_scanner"
    for package in ("patterns", "scoring", "paper", "backtest"):
        for py in (root / package).rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "fundamental" not in node.module, py
                if isinstance(node, ast.Import):
                    assert all("fundamental" not in a.name for a in node.names), py


def test_quarterly_and_annual_of_the_same_period_end_are_not_revisions(
    store: DuckDBStore, tmp_path: Path
) -> None:
    repo = DuckDBFundamentalRepository(store)
    q4 = parse_old_row({**OLD_ROW, "toDate": "31-Mar-2024", "seqNumber": "1"})
    annual = parse_old_row({**OLD_ROW, "toDate": "31-Mar-2024", "seqNumber": "2",
                            "period": "Annual"})  # fmt: skip
    assert q4 and annual
    fetch_filings(StubProvider([q4, annual]), repo, tmp_path, {"RELIANCE": "I"}, clock=lambda: NOW)
    rows = store.conn.execute("SELECT DISTINCT revision_number FROM fundamental_filings").fetchall()
    assert rows == [(0,)]
