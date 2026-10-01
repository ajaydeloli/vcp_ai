"""DuckDB-backed InstrumentRepository.

Implements the InstrumentRepository Protocol.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from vcp_scanner.domain.market import Instrument
from vcp_scanner.infrastructure.clock import Clock, utc_now

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class DuckDBInstrumentRepository:
    """InstrumentRepository backed by DuckDB."""

    def __init__(self, store: DuckDBStore, *, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock

    def save_instruments(self, instruments: list[Instrument]) -> int:
        """Upsert instruments into the 'instruments' table."""
        inserted = 0
        now_utc = self._clock()

        for inst in instruments:
            # We use an UPSERT (INSERT OR REPLACE) or DuckDB's ON CONFLICT
            # Note: DuckDB supports ON CONFLICT (instrument_id) DO UPDATE
            self._store.conn.execute(
                """
                INSERT INTO instruments (
                    instrument_id, isin, exchange, symbol,
                    company_name, segment, is_active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, TRUE, ?, ?)
                ON CONFLICT (instrument_id) DO UPDATE SET
                    -- Kite carries no ISIN; never overwrite a known ISIN with NULL.
                    isin = COALESCE(excluded.isin, instruments.isin),
                    exchange = excluded.exchange,
                    symbol = excluded.symbol,
                    company_name = excluded.company_name,
                    segment = excluded.segment,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                [
                    inst.instrument_id,
                    inst.isin,
                    inst.exchange,
                    inst.symbol,
                    inst.name,
                    inst.series,
                    now_utc,
                    now_utc,
                ],
            )
            inserted += 1

        return inserted

    def load_instruments(self) -> list[Instrument]:
        """Load all active instruments."""
        rows = self._store.conn.execute(
            """
            SELECT instrument_id, isin, exchange, symbol, company_name, segment
            FROM instruments
            WHERE is_active = TRUE
            """
        ).fetchall()

        instruments = []
        for row in rows:
            iid, isin, exchange, symbol, name, segment = row
            instruments.append(
                Instrument(
                    instrument_id=iid,
                    isin=isin,
                    exchange=exchange,
                    symbol=symbol,
                    name=name,
                    series=segment,
                )
            )
        return instruments


class DuckDBInstrumentResolver:
    """InstrumentResolver backed by the instruments and security_master_history tables.

    Lookup order is ISIN first, then exchange + symbol. In each case the instruments
    table is consulted before the (current rows of the) security master. ISIN is preferred
    because it survives a symbol rename, so a renamed instrument keeps its permanent ID
    (see vcp_scanner.data.identity).
    """

    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def resolve(
        self,
        *,
        isin: str | None,
        symbol: str | None,
        exchange: str = "NSE",
    ) -> str | None:
        conn = self._store.conn

        if isin:
            row = conn.execute(
                """
                SELECT instrument_id FROM instruments
                WHERE isin = ?
                ORDER BY is_active DESC, updated_at DESC
                LIMIT 1
                """,
                [isin],
            ).fetchone()
            if row:
                return str(row[0])

            # A former ISIN (before a face-value split): NSE's corporate-action feed keeps
            # quoting it, e.g. KPIGREEN's 2025 bonus under INE542W01017 (audit step 2.6).
            row = conn.execute(
                """
                SELECT instrument_id FROM instrument_identifier_history
                WHERE isin = ?
                ORDER BY valid_from DESC
                LIMIT 1
                """,
                [isin],
            ).fetchone()
            if row:
                return str(row[0])

            row = conn.execute(
                """
                SELECT instrument_id FROM security_master_history
                WHERE isin = ? AND known_to IS NULL
                ORDER BY valid_from ASC, known_from ASC
                LIMIT 1
                """,
                [isin],
            ).fetchone()
            if row:
                return str(row[0])

        if symbol:
            row = conn.execute(
                """
                SELECT instrument_id FROM instruments
                WHERE exchange = ? AND symbol = ?
                ORDER BY is_active DESC, updated_at DESC
                LIMIT 1
                """,
                [exchange, symbol],
            ).fetchone()
            if row:
                return str(row[0])

            row = conn.execute(
                """
                SELECT instrument_id FROM security_master_history
                WHERE exchange = ? AND symbol = ? AND known_to IS NULL
                ORDER BY valid_from ASC, known_from ASC
                LIMIT 1
                """,
                [exchange, symbol],
            ).fetchone()
            if row:
                return str(row[0])

        return None
