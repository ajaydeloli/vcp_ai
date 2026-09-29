"""Security Master & Surveillance Ingestion Worker (Phase 3).

Orchestrates fetching from SecurityMasterProvider and SurveillanceProvider,
saves raw records into the bitemporal security_master_history and
surveillance_flags_history tables, and closes stale flag rows when a
flag is no longer reported by the provider.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from vcp_scanner.data.identity import InstrumentResolver, canonical_instrument_id
from vcp_scanner.data.providers.base import (
    SecurityMasterProvider,
    SurveillanceProvider,
)

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class SecurityMasterIngestionWorker:
    """Orchestrates security master and surveillance flag ingestion.

    Responsibilities:
    1. Fetch current security master from the provider.
    2. Upsert into security_master_history (bitemporal: new rows get
       known_from=now, existing unchanged rows are left alone).
    3. Fetch current surveillance flags from the provider.
    4. Upsert into surveillance_flags_history, closing rows for flags
       that are no longer active.
    """

    # Source stamp for rows whose provider record carries no ``source`` of its own.
    PROVIDER_NAME = "NSE"

    def __init__(
        self,
        store: DuckDBStore,
        security_master_provider: SecurityMasterProvider,
        surveillance_provider: SurveillanceProvider,
        resolver: InstrumentResolver | None = None,
    ) -> None:
        self._store = store
        self._sm_provider = security_master_provider
        self._surv_provider = surveillance_provider
        self._resolver = resolver

    def run(
        self,
        start: date,
        end: date,
    ) -> dict[str, int]:
        """Run the full ingestion cycle.

        Args:
            start: Start of the date range to query providers.
            end: End of the date range to query providers.

        Returns:
            Dict with counts: security_inserted, security_unchanged,
            flags_inserted, flags_closed.
        """
        known_at = datetime.now(UTC)

        sm_stats = self._ingest_security_master(start, end, known_at)
        surv_stats = self._ingest_surveillance_flags(start, end, known_at)

        result = {**sm_stats, **surv_stats}
        logger.info("Security master ingestion complete: %s", result)
        return result

    # ------------------------------------------------------------------
    # Security Master
    # ------------------------------------------------------------------

    def _ingest_security_master(
        self,
        start: date,
        end: date,
        known_at: datetime,
    ) -> dict[str, int]:
        """Fetch and upsert security master records."""
        logger.info("Fetching security master from %s to %s", start, end)
        records = self._sm_provider.get_security_history(start, end)

        if not records:
            logger.warning("No security master records returned.")
            return {"security_inserted": 0, "security_unchanged": 0}

        # The NSE listing carries ISIN + symbol. Resolve to the permanent ID first so a
        # renamed symbol updates its existing instrument instead of forking a new one.
        records = [
            dataclasses.replace(
                rec,
                instrument_id=canonical_instrument_id(
                    self._resolver,
                    rec.instrument_id,
                    isin=rec.isin,
                    symbol=rec.symbol,
                    exchange=rec.exchange,
                ),
            )
            for rec in records
        ]

        inserted = 0
        unchanged = 0

        for rec in records:
            # The logical key of a history row is (instrument_id, valid_from): one instrument
            # can hold several periods (renames, series moves) side by side. Only the row
            # for the SAME period is ever superseded, so a multi-period history is kept
            # whole instead of collapsing to its last row.
            existing = self._store.conn.execute(
                """
                SELECT instrument_id, isin, series, exchange,
                       listing_date, delisting_date, symbol, valid_to
                FROM security_master_history
                WHERE instrument_id = ?
                  AND valid_from = ?
                  AND known_to IS NULL
                ORDER BY known_from DESC
                LIMIT 1
                """,
                [rec.instrument_id, rec.valid_from],
            ).fetchone()

            if existing:
                (
                    _,
                    ex_isin,
                    ex_series,
                    ex_exchange,
                    ex_listing,
                    ex_delisting,
                    ex_symbol,
                    ex_valid_to,
                ) = existing

                # If nothing changed, skip
                if (
                    ex_isin == rec.isin
                    and ex_series == rec.series
                    and ex_exchange == rec.exchange
                    and ex_listing == rec.listing_date
                    and ex_delisting == rec.delisting_date
                    and ex_symbol == rec.symbol
                    and ex_valid_to == rec.valid_to
                ):
                    unchanged += 1
                    continue

                # Something changed — supersede the old row
                self._store.conn.execute(
                    """
                    UPDATE security_master_history
                    SET known_to = ?
                    WHERE instrument_id = ?
                      AND valid_from = ?
                      AND known_to IS NULL
                    """,
                    [known_at, rec.instrument_id, rec.valid_from],
                )

            # Insert new row
            self._store.conn.execute(
                """
                INSERT INTO security_master_history (
                    instrument_id, isin, symbol, exchange,
                    listing_date, delisting_date, delisting_reason,
                    series, valid_from, valid_to,
                    source, known_from
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rec.instrument_id,
                    rec.isin,
                    rec.symbol,
                    rec.exchange,
                    rec.listing_date,
                    rec.delisting_date,
                    None,  # delisting_reason — not available from listing API
                    rec.series,
                    rec.valid_from,
                    rec.valid_to,
                    rec.source or self._sm_provider.__class__.__name__,
                    known_at,
                ],
            )
            inserted += 1

        logger.info(
            "Security master: %d inserted, %d unchanged",
            inserted,
            unchanged,
        )
        return {
            "security_inserted": inserted,
            "security_unchanged": unchanged,
        }

    # ------------------------------------------------------------------
    # Surveillance Flags
    # ------------------------------------------------------------------

    def _ingest_surveillance_flags(
        self,
        start: date,
        end: date,
        known_at: datetime,
    ) -> dict[str, int]:
        """Fetch and upsert surveillance flags, closing stale ones.

        The surveillance provider returns the *currently active* set of
        flags. If a flag that was previously open (known_to IS NULL) is
        no longer in the current set, we close it by setting known_to
        and valid_to.
        """
        logger.info("Fetching surveillance flags from %s to %s", start, end)
        # Surveillance lists carry no ISIN, so flags resolve by exchange + symbol.
        records = [
            dataclasses.replace(
                rec,
                instrument_id=canonical_instrument_id(self._resolver, rec.instrument_id),
            )
            for rec in self._surv_provider.get_flags(start, end)
        ]

        # Build set of currently active flags from provider
        active_flags: set[tuple[str, str]] = set()
        for rec in records:
            active_flags.add((rec.instrument_id, rec.flag))

        inserted = 0
        closed = 0

        # 1. Insert new flags that don't already exist
        for rec in records:
            stage = rec.extra.get("stage")
            existing = self._store.conn.execute(
                """
                SELECT stage
                FROM surveillance_flags_history
                WHERE instrument_id = ?
                  AND flag_type = ?
                  AND known_to IS NULL
                LIMIT 1
                """,
                [rec.instrument_id, rec.flag],
            ).fetchone()

            if existing:
                if existing[0] == stage:
                    # Flag already tracked with the same stage, skip
                    continue
                # ASM/GSM stage moved (e.g. II -> III): supersede the old row so the stage
                # history is kept and the universe sees the current stage.
                self._store.conn.execute(
                    """
                    UPDATE surveillance_flags_history
                    SET known_to = ?, valid_to = ?
                    WHERE instrument_id = ? AND flag_type = ? AND known_to IS NULL
                    """,
                    [known_at, rec.valid_from, rec.instrument_id, rec.flag],
                )

            self._store.conn.execute(
                """
                INSERT INTO surveillance_flags_history (
                    instrument_id, flag_type, stage,
                    valid_from, valid_to,
                    source, known_from
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rec.instrument_id,
                    rec.flag,
                    stage,
                    rec.valid_from,
                    rec.valid_to,
                    rec.source or self.PROVIDER_NAME,
                    known_at,
                ],
            )
            inserted += 1

        # 2. Close flags that are no longer active.
        # Absence from the feed only means "lapsed" if the provider returns the FULL set
        # active now (a snapshot, like NSE's ASM/T2T lists). A provider that returns event
        # history for the requested window sets ``returns_active_snapshot = False``; for
        # it, absence proves nothing, and closure would wrongly end flags that are still
        # active. Providers that do not declare it are treated as snapshots (legacy).
        snapshot = getattr(self._surv_provider, "returns_active_snapshot", True)
        if not snapshot:
            logger.info("Surveillance provider returns event history; not closing absent flags.")
            open_flags: list[tuple[str, str]] = []
        else:
            open_flags = self._store.conn.execute(
                """
                SELECT instrument_id, flag_type
                FROM surveillance_flags_history
                WHERE known_to IS NULL
                """,
            ).fetchall()

        today = known_at.date()
        for iid, flag_type in open_flags:
            if (iid, flag_type) not in active_flags:
                # Flag is no longer reported — close it
                self._store.conn.execute(
                    """
                    UPDATE surveillance_flags_history
                    SET known_to = ?,
                        valid_to = ?
                    WHERE instrument_id = ?
                      AND flag_type = ?
                      AND known_to IS NULL
                    """,
                    [known_at, today, iid, flag_type],
                )
                closed += 1

        logger.info(
            "Surveillance flags: %d inserted, %d closed",
            inserted,
            closed,
        )
        return {"flags_inserted": inserted, "flags_closed": closed}
