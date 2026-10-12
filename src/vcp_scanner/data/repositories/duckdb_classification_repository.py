"""Sector and industry per stock with validity dates (``instrument_classification``; S1).

Nothing is overwritten: a changed classification closes the open row (``valid_to``) and opens a
new one. A finer source (NSE's per-stock four-level classification, ``NSE_QUOTE``) is not
replaced by the coarser index lists while the sector agrees. A hand-entered sector (``MANUAL``)
never replaces an NSE one, and any NSE source replaces it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from vcp_scanner.data.storage.duckdb_store import DuckDBStore

SOURCE_INDEX_LIST = "NSE_INDEX_LIST"
SOURCE_QUOTE = "NSE_QUOTE"
SOURCE_MANUAL = "MANUAL"  # config/manual_sectors.yaml; any NSE source wins over it


@dataclass(frozen=True)
class Classification:
    instrument_id: str
    sector: str
    macro_sector: str | None = None
    industry: str | None = None
    basic_industry: str | None = None
    source: str = SOURCE_INDEX_LIST


@dataclass
class ApplyReport:
    inserted: int = 0
    changed: int = 0
    unchanged: int = 0
    kept_nse: int = 0  # manual entries not applied: NSE already classifies the stock
    #: (instrument_id, manual sector, NSE sector) where NSE replaced a different manual sector
    replaced_manual: list[tuple[str, str, str]] = field(default_factory=list)


class DuckDBClassificationRepository:
    def __init__(self, store: DuckDBStore) -> None:
        self._conn = store.conn

    def match_instruments(self, symbols_isins: Sequence[tuple[str, str | None]]) -> dict[str, str]:
        """Symbol -> instrument_id of active listings, by ISIN first, then by symbol."""
        rows = self._conn.execute(
            "SELECT instrument_id, symbol, isin FROM instruments WHERE is_active"
        ).fetchall()
        by_isin = {r[2]: r[0] for r in rows if r[2]}
        by_symbol = {r[1]: r[0] for r in rows}
        out: dict[str, str] = {}
        for symbol, isin in symbols_isins:
            iid = by_isin.get(isin) if isin else None
            iid = iid or by_symbol.get(symbol)
            if iid:
                out[symbol] = iid
        return out

    def current(self, instrument_id: str) -> Classification | None:
        row = self._conn.execute(
            "SELECT instrument_id, sector, macro_sector, industry, basic_industry,"
            " classification_source FROM instrument_classification"
            " WHERE instrument_id = ? AND valid_to IS NULL",
            [instrument_id],
        ).fetchone()
        return Classification(*row) if row else None

    def apply(self, items: Sequence[Classification], as_of: date) -> ApplyReport:
        report = ApplyReport()
        now = datetime.now(UTC)
        conn = self._conn
        conn.execute("BEGIN")
        try:
            for c in items:
                old = self.current(c.instrument_id)
                if old is not None:
                    if c.source == SOURCE_MANUAL and old.source != SOURCE_MANUAL:
                        report.kept_nse += 1  # NSE always wins over a hand-entered sector
                        continue
                    if (
                        old.source == SOURCE_MANUAL and c.source != SOURCE_MANUAL
                        and old.sector != c.sector
                    ):  # fmt: skip
                        report.replaced_manual.append((c.instrument_id, old.sector, c.sector))
                    coarser = c.source == SOURCE_INDEX_LIST and old.source == SOURCE_QUOTE
                    if old.sector == c.sector and (coarser or old == c):
                        report.unchanged += 1
                        continue
                    conn.execute(
                        "UPDATE instrument_classification SET valid_to = ?"
                        " WHERE instrument_id = ? AND valid_to IS NULL",
                        [as_of, c.instrument_id],
                    )
                    report.changed += 1
                else:
                    report.inserted += 1
                conn.execute(
                    "INSERT INTO instrument_classification VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    [c.instrument_id, c.macro_sector, c.sector, c.industry, c.basic_industry,
                     c.source, as_of, now],
                )  # fmt: skip
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return report

    def coverage(self, instrument_ids: Sequence[str]) -> int:
        if not instrument_ids:
            return 0
        row = self._conn.execute(
            "SELECT count(DISTINCT instrument_id) FROM instrument_classification"
            " WHERE valid_to IS NULL AND instrument_id IN (SELECT unnest(?))",
            [list(instrument_ids)],
        ).fetchone()
        return int(row[0]) if row else 0
