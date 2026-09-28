"""DuckDB-backed InstrumentRepository.

Implements the InstrumentRepository Protocol.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from vcp_scanner.domain.market import Instrument

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class DuckDBInstrumentRepository:
    """InstrumentRepository backed by DuckDB."""

    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def save_instruments(self, instruments: list[Instrument]) -> int:
        """Upsert instruments into the 'instruments' table."""
        inserted = 0
        now_utc = datetime.now(UTC)

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
                    isin = excluded.isin,
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
