"""NSE delisted-companies provider (PROJECT_DESIGN §14A; DATA_SPECIFICATION §4A).

Reads the exchange's own "List of Companies Delisted from NSE" workbook, published on
https://www.nseindia.com/static/list/list-of-companies-proposed-to-be-delisted, and turns it
into ``SecurityRecord`` rows that carry a ``delisting_date``. This is the first source in
the project that knows about securities which are no longer listed; ``EQUITY_L.csv``
(``NSESecurityMasterProvider``) only lists what trades today.

What the file is, verified against the published workbook (2026-09-28 revision):
  * one sheet named ``delisted`` with columns Symbol, ISIN, Company Name, Board,
    Delisted Date (Excel serial), Type of Delisting; ~457 rows from 2002 to 2026;
  * ISIN is ``Not Available`` for some old names;
  * NSE's own list does NOT include delistings by merger/amalgamation (e.g. HDFC Ltd,
    Mindtree and the 2019-20 PSU-bank mergers are absent). It is therefore a PARTIAL
    delisting source and cannot by itself support ``POINT_IN_TIME_COMPLETE``;
  * it has no listing date and no series, so both stay ``None`` on the records.

The download URL changes with every revision of the file (the file name embeds upload
timestamps), so it is discovered from the page. If discovery is blocked, pass a downloaded
copy with ``file_path`` instead of guessing a URL.

The workbook is read with the standard library (zip + XML) to avoid adding a dependency for
one sheet.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import requests

from vcp_scanner.data.identity import mint_instrument_id
from vcp_scanner.data.providers.nse_security_master import _parse_nse_date
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import SecurityRecord

logger = logging.getLogger(__name__)

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_EXCEL_EPOCH = date(1899, 12, 30)

_REQUIRED_HEADERS = ("symbol", "isin", "delisted date", "type of delisting")
_NO_ISIN = {"", "not available", "na", "n/a", "-", "none"}


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    if letters is None:
        raise ValueError(f"bad cell reference: {cell_ref!r}")
    index = 0
    for ch in letters.group():
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _read_sheet_rows(payload: bytes, preferred_sheet: str) -> list[list[str | None]]:
    """Return the rows of one worksheet as lists of cell text (numbers as their text)."""
    try:
        book = zipfile.ZipFile(io.BytesIO(payload))
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = [
                "".join(t.text or "" for t in si.iter(f"{{{_MAIN_NS}}}t"))
                for si in root.findall(f"{{{_MAIN_NS}}}si")
            ]
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {
            r.attrib["Id"]: r.attrib["Target"]
            for r in rels.findall(f"{{{_PKG_REL_NS}}}Relationship")
        }
        sheets = workbook.find(f"{{{_MAIN_NS}}}sheets")
        if sheets is None or len(sheets) == 0:
            raise ProviderError("NSE delisted workbook has no sheets")
        chosen = next(
            (s for s in sheets if s.attrib.get("name", "").strip().lower() == preferred_sheet),
            sheets[0],
        )
        target = targets[chosen.attrib[f"{{{_REL_NS}}}id"]].lstrip("/")
        sheet_path = target if target.startswith("xl/") else f"xl/{target}"
        sheet_root = ET.fromstring(book.read(sheet_path))
    except ProviderError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as err:
        raise ProviderError(f"NSE delisted workbook is not a readable .xlsx: {err}") from err

    rows: list[list[str | None]] = []
    for row in sheet_root.iter(f"{{{_MAIN_NS}}}row"):
        cells: list[str | None] = []
        for cell in row.findall(f"{{{_MAIN_NS}}}c"):
            idx = _column_index(cell.attrib["r"])
            cells.extend([None] * (idx + 1 - len(cells)))
            value = cell.find(f"{{{_MAIN_NS}}}v")
            if value is None or value.text is None:
                continue
            cells[idx] = shared[int(value.text)] if cell.attrib.get("t") == "s" else value.text
        rows.append(cells)
    return rows


def _to_date(raw: str | None) -> date | None:
    """Excel serial (the published form) or one of NSE's text date formats."""
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    try:
        return _EXCEL_EPOCH + timedelta(days=int(float(text)))
    except ValueError:
        return _parse_nse_date(text)


class NSEDelistedProvider:
    """``SecurityMasterProvider`` for securities NSE has delisted."""

    PROVIDER_NAME = "NSE_DELISTED_LIST"
    PAGE_URL = "https://www.nseindia.com/static/list/list-of-companies-proposed-to-be-delisted"
    _LINK_RE = re.compile(
        r"src=(https%3A%2F%2F[^&\"'\s]*?list_of_delisted_companies[^&\"'\s]*?\.xlsx)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        file_path: Path | str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._file_path = Path(file_path) if file_path is not None else None
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
                "Referer": "https://www.nseindia.com/",
            }
        )

    # ------------------------------------------------------------------ loading

    def _discover_url(self) -> str:
        try:
            resp = self._session.get(self.PAGE_URL, timeout=20)
        except requests.RequestException as err:
            raise ProviderError(f"Could not open NSE delisting page: {err}") from err
        if resp.status_code != 200:
            raise ProviderError(f"NSE delisting page returned HTTP {resp.status_code}")
        match = self._LINK_RE.search(resp.text)
        if match is None:
            raise ProviderError(
                "Could not find the 'List of Companies Delisted from NSE' link on the NSE "
                "delisting page (layout changed?). Download the .xlsx manually and pass it "
                "with --delisted-file."
            )
        return unquote(match.group(1))

    def _load_payload(self) -> bytes:
        if self._file_path is not None:
            try:
                return self._file_path.read_bytes()
            except OSError as err:
                raise ProviderError(f"Cannot read delisted file {self._file_path}: {err}") from err
        url = self._discover_url()
        try:
            resp = self._session.get(url, timeout=60)
        except requests.RequestException as err:
            raise ProviderError(f"Could not download NSE delisted list from {url}: {err}") from err
        if resp.status_code != 200 or not resp.content:
            raise ProviderError(f"NSE delisted list download returned HTTP {resp.status_code}")
        return resp.content

    # ------------------------------------------------------------------ parsing

    def get_security_history(self, start: date, end: date) -> list[SecurityRecord]:
        """Return one delisting record per delisted security with delisting date >= ``start``.

        ``end`` is not used as a filter: a delisting is a fact about the security whether or
        not it falls inside the requested window, and ingestion is bitemporal (``known_from``
        records when we learned it).

        ``valid_from`` is ``start`` because the file carries no listing date; the record
        asserts only "listed until ``delisting_date``", with ``listing_date`` left ``None``.
        """
        rows = _read_sheet_rows(self._load_payload(), preferred_sheet="delisted")
        if not rows:
            raise ProviderError("NSE delisted workbook is empty")

        header = [(h or "").strip().lower() for h in rows[0]]
        missing = [h for h in _REQUIRED_HEADERS if h not in header]
        if missing:
            raise ProviderError(
                f"NSE delisted workbook is missing expected columns {missing}; found {header}"
            )
        col = {name: header.index(name) for name in _REQUIRED_HEADERS}

        def cell(row: list[str | None], name: str) -> str:
            i = col[name]
            value = row[i] if i < len(row) else None
            return value.strip() if value is not None else ""

        # One entry per security. The same ISIN (or, without one, symbol) can appear twice
        # (VIKASHMET; HUSYS/HUSYSLTD after a platform move). They would map to one
        # instrument_id, so keep the latest delisting: the security's final exit.
        latest: dict[str, tuple[date, str, str | None, str | None]] = {}
        collapsed: list[str] = []
        bad_rows = 0
        for row in rows[1:]:
            symbol = cell(row, "symbol").upper()
            if not symbol:
                continue
            delisted_on = _to_date(cell(row, "delisted date"))
            if delisted_on is None:
                bad_rows += 1
                logger.warning("NSE delisted list: %s has no parsable delisted date", symbol)
                continue
            isin_raw = cell(row, "isin")
            isin = None if isin_raw.lower() in _NO_ISIN else isin_raw
            reason = cell(row, "type of delisting") or None
            key = isin or f"SYMBOL:{symbol}"
            previous = latest.get(key)
            if previous is not None:
                collapsed.append(f"{previous[1]}+{symbol}")
                if previous[0] >= delisted_on:
                    continue
            latest[key] = (delisted_on, symbol, isin, reason)

        if collapsed:
            logger.warning(
                "NSE delisted list: %d security(ies) listed more than once; kept the latest "
                "delisting for: %s",
                len(collapsed),
                ", ".join(collapsed),
            )
        if not latest:
            raise ProviderError("NSE delisted workbook parsed to zero delisted securities")

        records = [
            SecurityRecord(
                instrument_id=mint_instrument_id("NSE", symbol),
                symbol=symbol,
                exchange="NSE",
                valid_from=start,
                valid_to=delisted_on,
                isin=isin,
                series=None,
                listing_date=None,
                delisting_date=delisted_on,
                source=self.PROVIDER_NAME,
                delisting_reason=reason,
            )
            for delisted_on, symbol, isin, reason in latest.values()
            if delisted_on >= start
        ]
        logger.info(
            "NSE delisted list: %d delisted securities on or after %s (%d unparsable rows)",
            len(records),
            start,
            bad_rows,
        )
        return records
