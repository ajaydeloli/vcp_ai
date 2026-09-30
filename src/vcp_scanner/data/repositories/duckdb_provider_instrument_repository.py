"""DuckDB-backed ProviderInstrumentRepository (DATABASE_SCHEMA section 10; audit P1-1).

Maps a provider's own identifiers (Kite instrument token, ...) to the permanent
``instrument_id`` with validity dates, so token reuse, symbol changes and a second broker
can be handled without touching strategy code.

Semantics of ``sync_full_dump`` (a dump lists what is tradable *today*):

* a token seen for the first time is opened with ``valid_from = as_of``;
* a token already open for the same instrument and symbol is left alone;
* a token now pointing at a different instrument or symbol is closed at ``as_of`` and a
  new row is opened (same-day re-sync updates in place: the key contains ``valid_from``);
* a token absent from the dump is closed at ``as_of``.

``valid_from`` is the first day the mapping was *observed*: dumps carry no earlier history.
What a bar was really fetched with is recorded per row in ``raw_ohlcv.provider_instrument_id``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

from vcp_scanner.domain.market import ProviderInstrumentMapping, ProviderMappingSyncResult
from vcp_scanner.infrastructure.clock import Clock, utc_now

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class DuckDBProviderInstrumentRepository:
    """``ProviderInstrumentRepository`` backed by the ``provider_instruments`` table."""

    def __init__(self, store: DuckDBStore, *, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock

    def sync_full_dump(
        self,
        provider: str,
        mappings: Sequence[ProviderInstrumentMapping],
        as_of: date,
        *,
        skipped_unresolved: int = 0,
    ) -> ProviderMappingSyncResult:
        if not mappings:
            raise ValueError(
                f"Refusing to sync an empty {provider} instrument dump: it would close every "
                "open mapping."
            )
        wrong = {m.provider for m in mappings if m.provider != provider}
        if wrong:
            raise ValueError(f"Mappings for {sorted(wrong)} passed to a {provider} sync")

        # One entry per token: a dump listing the same token twice keeps the last row.
        incoming = {m.provider_instrument_id: m for m in mappings}
        conn = self._store.conn
        now = self._clock()
        opened = changed = closed = unchanged = 0

        conn.execute("BEGIN TRANSACTION")
        try:
            open_rows = conn.execute(
                """
                SELECT provider_instrument_id, instrument_id, provider_symbol, exchange,
                       valid_from
                FROM provider_instruments
                WHERE provider = ? AND valid_to IS NULL
                """,
                [provider],
            ).fetchall()
            current = {r[0]: r for r in open_rows}

            for token, mapping in incoming.items():
                row = current.get(token)
                if row is not None:
                    _, instrument_id, symbol, exchange, valid_from = row
                    if (
                        instrument_id == mapping.instrument_id
                        and symbol == mapping.provider_symbol
                        and exchange == mapping.exchange
                    ):
                        unchanged += 1
                        continue
                    self._close(provider, token, valid_from, as_of)
                    changed += 1
                    logger.warning(
                        "%s token %s re-pointed: %s/%s -> %s/%s",
                        provider,
                        token,
                        instrument_id,
                        symbol,
                        mapping.instrument_id,
                        mapping.provider_symbol,
                    )
                else:
                    opened += 1
                self._open(mapping, as_of, now)

            for token, row in current.items():
                if token not in incoming:
                    self._close(provider, token, row[4], as_of)
                    closed += 1
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

        result = ProviderMappingSyncResult(
            opened=opened,
            changed=changed,
            closed=closed,
            unchanged=unchanged,
            skipped_unresolved=skipped_unresolved,
        )
        logger.info("Synced %s instrument mappings: %s", provider, result)
        return result

    def _close(self, provider: str, token: str, valid_from: date, as_of: date) -> None:
        """Close an open mapping. A row opened today and changed today is removed instead,
        because ``valid_from`` is part of the key and an empty interval carries no meaning."""
        if valid_from >= as_of:
            self._store.conn.execute(
                "DELETE FROM provider_instruments"
                " WHERE provider = ? AND provider_instrument_id = ? AND valid_from = ?",
                [provider, token, valid_from],
            )
        else:
            self._store.conn.execute(
                "UPDATE provider_instruments SET valid_to = ?"
                " WHERE provider = ? AND provider_instrument_id = ? AND valid_from = ?",
                [as_of, provider, token, valid_from],
            )

    def _open(self, m: ProviderInstrumentMapping, as_of: date, now: object) -> None:
        self._store.conn.execute(
            """
            INSERT INTO provider_instruments (
                provider, provider_instrument_id, instrument_id, provider_symbol,
                exchange, valid_from, valid_to, metadata_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            [
                m.provider,
                m.provider_instrument_id,
                m.instrument_id,
                m.provider_symbol,
                m.exchange,
                as_of,
                json.dumps(m.metadata, sort_keys=True) if m.metadata else None,
                now,
            ],
        )

    def instrument_id_for(
        self, provider: str, provider_instrument_id: str, on_date: date
    ) -> str | None:
        row = self._store.conn.execute(
            """
            SELECT instrument_id FROM provider_instruments
            WHERE provider = ? AND provider_instrument_id = ?
              AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
            ORDER BY valid_from DESC LIMIT 1
            """,
            [provider, provider_instrument_id, on_date, on_date],
        ).fetchone()
        return str(row[0]) if row else None

    def provider_instrument_id_for(
        self, provider: str, instrument_id: str, on_date: date
    ) -> str | None:
        row = self._store.conn.execute(
            """
            SELECT provider_instrument_id FROM provider_instruments
            WHERE provider = ? AND instrument_id = ?
              AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
            ORDER BY valid_from DESC, provider_instrument_id LIMIT 1
            """,
            [provider, instrument_id, on_date, on_date],
        ).fetchone()
        return str(row[0]) if row else None

    def load_open(self, provider: str) -> list[ProviderInstrumentMapping]:
        rows = self._store.conn.execute(
            """
            SELECT provider_instrument_id, instrument_id, provider_symbol, exchange,
                   metadata_json
            FROM provider_instruments
            WHERE provider = ? AND valid_to IS NULL
            ORDER BY provider_instrument_id
            """,
            [provider],
        ).fetchall()
        return [
            ProviderInstrumentMapping(
                provider=provider,
                provider_instrument_id=r[0],
                instrument_id=r[1],
                provider_symbol=r[2],
                exchange=r[3],
                metadata=json.loads(r[4]) if r[4] else None,
            )
            for r in rows
        ]
