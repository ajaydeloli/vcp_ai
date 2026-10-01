"""Tests for the NSE delisted-companies source (audit P0-3).

All workbooks here are SYNTHETIC: built in-memory with the same layout as NSE's published
"List of Companies Delisted from NSE" file (sheet ``delisted``; Symbol, ISIN, Company Name,
Board, Delisted Date as Excel serial, Type of Delisting). No live NSE access.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

import pytest

from vcp_scanner.data.ingestion.sm_worker import SecurityMasterIngestionWorker
from vcp_scanner.data.providers.nse_delisted import NSEDelistedProvider
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import derive_survivorship
from vcp_scanner.domain.enums import SurvivorshipStatus
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import SecurityRecord

HEADER = ["Symbol", "ISIN", "Company Name", "Board", "Delisted Date", "Type of Delisting"]


def _serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


def make_xlsx(rows: list[list[object]], *, sheet_name: str = "delisted") -> bytes:
    """Minimal .xlsx: strings go to sharedStrings, numbers stay numeric (like NSE's file)."""
    shared: list[str] = []

    def sst(text: str) -> int:
        if text not in shared:
            shared.append(text)
        return shared.index(text)

    row_xml = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(ord('A') + c)}{r}"
            if value is None:
                continue
            if isinstance(value, int | float):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{sst(str(value))}</v></c>')
        row_xml.append(f'<row r="{r}">{"".join(cells)}</row>')

    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": (
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
            f'<sheet name="Other" sheetId="1" r:id="rId9"/>'
            f'<sheet name="{sheet_name}" sheetId="2" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships xmlns="{pkg}">'
            f'<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId9" Target="worksheets/sheet2.xml"/></Relationships>'
        ),
        "xl/sharedStrings.xml": (
            f'<sst xmlns="{main}">'
            + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared)
            + "</sst>"
        ),
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{main}"><sheetData>{"".join(row_xml)}</sheetData></worksheet>'
        ),
        # A decoy first sheet: must not be read when a sheet named "delisted" exists.
        "xl/worksheets/sheet2.xml": (
            f'<worksheet xmlns="{main}"><sheetData><row r="1"><c r="A1"><v>1</v></c></row>'
            f"</sheetData></worksheet>"
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _row(symbol, isin, delisted, kind="Compulsory Delisting ", board="Main Board"):
    return [symbol, isin, f"{symbol} Ltd", board, _serial(delisted), kind]


@pytest.fixture()
def delisted_file(tmp_path: Path):
    def _write(rows, **kw) -> Path:
        path = tmp_path / "delisted.xlsx"
        path.write_bytes(make_xlsx([HEADER, *rows], **kw))
        return path

    return _write


START = date(2000, 1, 1)
END = date(2026, 9, 30)


# --------------------------------------------------------------------------- provider


def test_parses_delisting_records(delisted_file):
    path = delisted_file(
        [
            _row("OLDCO", "INE000A01011", date(2017, 3, 24), "Compulsory Delisting "),
            _row("NOISIN", "Not Available", date(2010, 5, 1), "Delisting - Liquidation"),
        ]
    )
    records = {
        r.symbol: r for r in NSEDelistedProvider(file_path=path).get_security_history(START, END)
    }

    old = records["OLDCO"]
    assert old.instrument_id == "NSE_EQ|OLDCO"
    assert old.isin == "INE000A01011"
    assert old.delisting_date == date(2017, 3, 24)
    assert old.valid_to == date(2017, 3, 24)
    assert old.valid_from == START
    assert old.delisting_reason == "Compulsory Delisting"
    assert old.listing_date is None and old.series is None  # not in the file; never invented
    assert old.source == "NSE_DELISTED_LIST"

    assert records["NOISIN"].isin is None  # "Not Available" is not an ISIN


def test_only_delistings_on_or_after_start(delisted_file):
    path = delisted_file(
        [
            _row("OLD", "INE000A01011", date(2004, 1, 1)),
            _row("NEW", "INE000B01011", date(2018, 1, 1)),
        ]
    )
    got = NSEDelistedProvider(file_path=path).get_security_history(date(2010, 1, 1), END)
    assert [r.symbol for r in got] == ["NEW"]


def test_same_isin_listed_twice_keeps_latest_delisting(delisted_file):
    path = delisted_file(
        [
            _row("HUSYS", "INE336T01010", date(2016, 6, 1), "Exit from ITP platform", "ITP"),
            _row("HUSYSLTD", "INE336T01010", date(2021, 7, 12), "Voluntary Delisting ", "SME"),
        ]
    )
    got = NSEDelistedProvider(file_path=path).get_security_history(START, END)
    assert [(r.symbol, r.delisting_date) for r in got] == [("HUSYSLTD", date(2021, 7, 12))]


def test_same_symbol_without_isin_keeps_latest_delisting(delisted_file):
    path = delisted_file(
        [
            _row("DUP", "Not Available", date(2012, 1, 1)),
            _row("DUP", "Not Available", date(2014, 1, 1)),
        ]
    )
    got = NSEDelistedProvider(file_path=path).get_security_history(START, END)
    assert [(r.symbol, r.delisting_date) for r in got] == [("DUP", date(2014, 1, 1))]


def test_reads_sheet_named_delisted_not_first_sheet(delisted_file):
    path = delisted_file([_row("OLDCO", "INE000A01011", date(2017, 3, 24))])
    assert len(NSEDelistedProvider(file_path=path).get_security_history(START, END)) == 1


def test_missing_expected_column_is_a_provider_error(tmp_path):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(make_xlsx([["Symbol", "ISIN"], ["A", "INE000A01011"]]))
    with pytest.raises(ProviderError, match="missing expected columns"):
        NSEDelistedProvider(file_path=path).get_security_history(START, END)


def test_header_only_workbook_is_not_an_empty_success(delisted_file):
    with pytest.raises(ProviderError, match="zero delisted"):
        NSEDelistedProvider(file_path=delisted_file([])).get_security_history(START, END)


def test_not_an_xlsx_is_a_provider_error(tmp_path):
    path = tmp_path / "x.xlsx"
    path.write_bytes(b"<html>blocked</html>")
    with pytest.raises(ProviderError, match="not a readable"):
        NSEDelistedProvider(file_path=path).get_security_history(START, END)


def test_missing_file_is_a_provider_error(tmp_path):
    with pytest.raises(ProviderError, match="Cannot read"):
        NSEDelistedProvider(file_path=tmp_path / "nope.xlsx").get_security_history(START, END)


class _Resp:
    def __init__(self, status=200, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content


class _Session:
    def __init__(self, page: _Resp, download: _Resp):
        self.headers: dict[str, str] = {}
        self._page, self._download, self.urls = page, download, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self._page if url == NSEDelistedProvider.PAGE_URL else self._download


def test_download_url_is_discovered_from_the_page():
    xlsx_url = (
        "https://nsearchives.nseindia.com//web/mediaattachment/2026-09/"
        "Copy_of_List_of_delisted_Companies_20260928.xlsx"
    )
    decoy = "https://nsearchives.nseindia.com//web/mediaattachment/2026-09/Companies_proposed_to_be_delisted_list.xlsx"
    viewer = "https://view.officeapps.live.com/op/view.aspx?src="
    html = (
        f'<a href="{viewer}{quote(decoy, safe="")}&wdOrigin=X">p</a>'
        f'<a href="{viewer}{quote(xlsx_url, safe="")}&wdOrigin=X">d</a>'
    )
    payload = make_xlsx([HEADER, _row("OLDCO", "INE000A01011", date(2017, 3, 24))])
    session = _Session(_Resp(text=html), _Resp(content=payload))

    got = NSEDelistedProvider(session=session).get_security_history(START, END)  # type: ignore[arg-type]

    assert [r.symbol for r in got] == ["OLDCO"]
    assert session.urls[-1] == xlsx_url  # the "delisted" list, not "proposed to be delisted"


def test_page_without_link_points_to_manual_file():
    session = _Session(_Resp(text="<html>redesigned</html>"), _Resp())
    with pytest.raises(ProviderError, match="--delisted-file"):
        NSEDelistedProvider(session=session).get_security_history(START, END)  # type: ignore[arg-type]


def test_blocked_page_is_a_provider_error():
    session = _Session(_Resp(status=403), _Resp())
    with pytest.raises(ProviderError, match="HTTP 403"):
        NSEDelistedProvider(session=session).get_security_history(START, END)  # type: ignore[arg-type]


# ------------------------------------------------------------------------- ingestion


class _LiveProvider:
    def __init__(self, records):
        self._records = records

    def get_security_history(self, start, end):
        return self._records


class _NoFlags:
    def get_flags(self, start, end):
        return []


class _Delisted:
    def __init__(self, records):
        self._records = records

    def get_security_history(self, start, end):
        return self._records


def _live(symbol, isin, listed=date(2010, 1, 1)):
    return SecurityRecord(
        instrument_id=f"NSE_EQ|{symbol}",
        symbol=symbol,
        exchange="NSE",
        valid_from=listed,
        isin=isin,
        series="EQ",
        listing_date=listed,
        source="NSE",
    )


def _gone(symbol, isin, delisted=date(2018, 5, 1)):
    return SecurityRecord(
        instrument_id=f"NSE_EQ|{symbol}",
        symbol=symbol,
        exchange="NSE",
        valid_from=START,
        valid_to=delisted,
        isin=isin,
        delisting_date=delisted,
        source="NSE_DELISTED_LIST",
        delisting_reason="Compulsory Delisting",
    )


@pytest.fixture()
def store():
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


CLOCK = lambda: datetime(2026, 9, 30, tzinfo=UTC)  # noqa: E731


def _worker(store, live, delisted):
    return SecurityMasterIngestionWorker(
        store,
        _LiveProvider(live),
        _NoFlags(),
        delisting_provider=_Delisted(delisted) if delisted is not None else None,
        clock=CLOCK,
    )


def _rows(store):
    return store.conn.execute(
        "SELECT instrument_id, isin, delisting_date, valid_to, delisting_reason, source "
        "FROM security_master_history WHERE known_to IS NULL ORDER BY instrument_id"
    ).fetchall()


def test_delisted_rows_are_stored_with_date_and_reason(store):
    stats = _worker(store, [_live("LIVE", "INE111A01011")], [_gone("GONE", "INE222B01011")]).run(
        START, END
    )

    assert stats["delisted_records"] == 1 and stats["delisted_skipped"] == 0
    assert _rows(store) == [
        ("NSE_EQ|GONE", "INE222B01011", date(2018, 5, 1), date(2018, 5, 1),
         "Compulsory Delisting", "NSE_DELISTED_LIST"),
        ("NSE_EQ|LIVE", "INE111A01011", None, None, None, "NSE"),
    ]  # fmt: skip


def test_rerun_is_idempotent(store):
    w = _worker(store, [_live("LIVE", "INE111A01011")], [_gone("GONE", "INE222B01011")])
    w.run(START, END)
    stats = w.run(START, END)
    assert stats["security_inserted"] == 0 and stats["security_unchanged"] == 2


def test_without_delisting_provider_stats_and_behaviour_unchanged(store):
    stats = _worker(store, [_live("LIVE", "INE111A01011")], None).run(START, END)
    assert "delisted_records" not in stats
    assert len(_rows(store)) == 1


def test_symbol_reused_by_a_different_company_is_not_merged(store):
    """Old company 'REUSED' (ISIN Y) delisted; a new company now trades as REUSED (ISIN X)."""
    stats = _worker(
        store, [_live("REUSED", "INE111A01011")], [_gone("REUSED", "INE999Z01011")]
    ).run(START, END)

    assert stats["delisted_records"] == 0 and stats["delisted_skipped"] == 1
    rows = _rows(store)
    assert len(rows) == 1 and rows[0][2] is None  # the live company was not marked delisted


def test_delisted_without_isin_on_a_live_symbol_is_skipped(store):
    stats = _worker(store, [_live("REUSED", "INE111A01011")], [_gone("REUSED", None)]).run(
        START, END
    )
    assert stats["delisted_skipped"] == 1


def test_relisted_same_isin_keeps_both_periods(store):
    stats = _worker(
        store,
        [_live("BACK", "INE333C01011", listed=date(2022, 1, 1))],
        [_gone("BACK", "INE333C01011", delisted=date(2015, 1, 1))],
    ).run(START, END)

    assert stats["delisted_records"] == 1
    assert len(store.conn.execute("SELECT * FROM security_master_history").fetchall()) == 2


def test_earlier_live_row_with_another_isin_blocks_delisted_record(store):
    _worker(store, [_live("HELD", "INE111A01011")], None).run(START, END)
    # Next run: the live feed no longer lists HELD, but the database still holds it.
    stats = _worker(store, [_live("OTHER", "INE444D01011")], [_gone("HELD", "INE555E01011")]).run(
        START, END
    )
    assert stats["delisted_skipped"] == 1


def test_ingesting_delisted_names_moves_survivorship_off_biased(store):
    repo = DuckDBUniverseRepository(store)

    def status() -> SurvivorshipStatus:
        as_of = CLOCK().date()
        evidence = repo.survivorship_evidence(as_of, CLOCK(), as_of - timedelta(days=380))
        return derive_survivorship(evidence, as_of)[0]

    _worker(store, [_live("LIVE", "INE111A01011")], None).run(START, END)
    assert status().value == "BIASED"

    _worker(store, [_live("LIVE", "INE111A01011")], [_gone("GONE", "INE222B01011")]).run(START, END)
    # Delisting records without bhavcopy prices: still not complete (audit P0-4).
    assert status().value == "PARTIAL"
    assert not status().may_validate_thresholds


def test_same_issuer_relisted_under_a_later_isin_keeps_both_periods(store):
    """DHFL (INE202B01012) delisted 2021; the same issuer trades again as INE202B01038 under
    the same instrument (PIRAMALFIN). Its delisting is an earlier period, not a reused symbol."""
    stats = _worker(
        store,
        [_live("BACK", "INE202B01038", listed=date(2025, 11, 7))],
        [_gone("BACK", "INE202B01012", delisted=date(2021, 9, 29))],
    ).run(START, END)

    assert stats["delisted_records"] == 1 and stats["delisted_skipped"] == 0
    rows = _rows(store)
    assert {(r[1], r[2]) for r in rows} == {("INE202B01012", date(2021, 9, 29)),
                                            ("INE202B01038", None)}  # fmt: skip
