"""DuckDB-backed MarketDataRepository (DATABASE_SCHEMA §14; AGENTS.md rules 2, 3).

Implements the ``MarketDataRepository`` Protocol defined in
``vcp_scanner.data.repositories.base``.

Bitemporal contract:
- ``save_daily`` is append-only.  When a bar for the same (instrument_id,
  trade_date) already exists, the old row's ``known_to`` is set to the
  injection timestamp and a new row is inserted with ``known_to = NULL``.
- ``load_daily`` returns only *current* rows (``known_to IS NULL``).
- Reads for point-in-time reproducibility use ``load_daily_as_of``.

Rules:
- No ``datetime.now()`` — timestamps are injected by the caller (rule 1).
- raw_ohlcv is insert-only; no UPDATE/DELETE (rule 2).
- Strategy code never imports this module; it holds only the interface (rule 3).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from vcp_scanner.data.schema import (
    DailyPriceAdjustedRow,
    IngestionRunRow,
    OHLCValidationResult,
    RawOHLCVRow,
    candle_source_hash,
    validate_ohlc,
)
from vcp_scanner.domain.enums import Timeframe
from vcp_scanner.domain.market import Candle
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.infrastructure.clock import Clock, utc_now

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class DuckDBMarketDataRepository:
    """``MarketDataRepository`` backed by DuckDB (DATABASE_SCHEMA §12, §14).

    Injected with a ``DuckDBStore`` by the application wiring layer.
    Tests inject an in-memory store so no filesystem I/O occurs.
    """

    def __init__(self, store: DuckDBStore, *, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock

    # ------------------------------------------------------------------
    # MarketDataRepository protocol methods
    # ------------------------------------------------------------------

    def load_daily(
        self,
        instrument_id: str,
        start: date,
        end: date,
    ) -> list[Candle]:
        """Return current canonical daily bars in [start, end] (inclusive).

        Only rows with ``known_to IS NULL`` are returned — i.e. the current
        truth for each trade_date.  Superseded corrections are invisible here;
        use ``load_daily_as_of`` for point-in-time reads.
        """
        rows = self._store.conn.execute(
            """
            SELECT
                instrument_id, trade_date,
                open_raw, high_raw, low_raw, close_raw,
                volume_raw, primary_provider
            FROM daily_prices
            WHERE instrument_id = ?
              AND trade_date   >= ?
              AND trade_date   <= ?
              AND known_to IS NULL
            ORDER BY trade_date
            """,
            [instrument_id, start, end],
        ).fetchall()

        candles: list[Candle] = []
        for row in rows:
            iid, td, open_val, high_val, low_val, close_val, vol, prov = row
            candles.append(
                Candle(
                    instrument_id=iid,
                    timestamp=datetime(td.year, td.month, td.day, tzinfo=UTC),
                    timeframe=Timeframe.DAILY,
                    open=open_val,
                    high=high_val,
                    low=low_val,
                    close=close_val,
                    volume=vol,
                    provider=prov,
                )
            )
        return candles

    def save_daily(self, candles: list[Candle]) -> int:
        """Append-only save of daily candles.  Bitemporal on conflict.

        For each candle:
        1. Validate OHLC (DATA_SPECIFICATION §23). Reject on failure.
        2. Compute source_hash for dedup.
        3. If a *current* row (known_to IS NULL) already exists for the same
           (instrument_id, trade_date), close it by setting ``known_to`` to
           ``now_utc`` (the ingestion timestamp passed in candle.ingested_at,
           or the injected clock as fallback).
        4. Insert new row with ``known_from = now_utc``, ``known_to = NULL``.

        Returns the number of new rows actually inserted (rejections not counted).
        """
        now_utc = self._clock()
        inserted = 0

        for candle in candles:
            validation: OHLCValidationResult = validate_ohlc(
                candle.open, candle.high, candle.low, candle.close, candle.volume
            )
            if not validation.is_valid:
                logger.warning(
                    "OHLC validation failed for %s %s — skipping. Reason: %s",
                    candle.instrument_id,
                    candle.timestamp.date(),
                    validation.reason,
                )
                continue

            trade_date = candle.timestamp.date()
            known_from = candle.ingested_at or now_utc
            src_hash = candle_source_hash(
                instrument_id=candle.instrument_id,
                timestamp_iso=candle.timestamp.isoformat(),
                timeframe=str(candle.timeframe),
                open_=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                provider=candle.provider,
            )

            # --- dedup: if hash already exists and row is current, skip ---
            existing = self._store.conn.execute(
                """
                SELECT source_hash FROM daily_prices
                WHERE instrument_id = ? AND trade_date = ? AND known_to IS NULL
                """,
                [candle.instrument_id, trade_date],
            ).fetchone()

            if existing is not None and existing[0] == src_hash:
                # Exact duplicate — idempotent, skip silently.
                continue

            # --- close the superseded current row if one exists ---
            if existing is not None:
                self._store.conn.execute(
                    """
                    UPDATE daily_prices
                    SET known_to = ?
                    WHERE instrument_id = ? AND trade_date = ? AND known_to IS NULL
                    """,
                    [known_from, candle.instrument_id, trade_date],
                )

            # --- insert new current row ---
            self._store.conn.execute(
                """
                INSERT INTO daily_prices (
                    instrument_id, trade_date,
                    open_raw, high_raw, low_raw, close_raw, volume_raw,
                    primary_provider, data_status,
                    source_run_id, source_hash,
                    known_from, known_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                [
                    candle.instrument_id,
                    trade_date,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.provider,
                    "OK",
                    candle.provider_request_id or "unknown",
                    src_hash,
                    known_from,
                ],
            )
            inserted += 1

        return inserted

    def latest_timestamp(self, instrument_id: str) -> datetime | None:
        """Return the latest trade_date recorded for this instrument (current rows only).

        Returns ``None`` if no data exists — never returns a fabricated date.
        """
        row = self._store.conn.execute(
            """
            SELECT MAX(trade_date) FROM daily_prices
            WHERE instrument_id = ? AND known_to IS NULL
            """,
            [instrument_id],
        ).fetchone()

        if row is None or row[0] is None:
            return None
        d = row[0]  # DuckDB returns a date object
        return datetime(d.year, d.month, d.day, tzinfo=UTC)

    def earliest_timestamp(self, instrument_id: str) -> datetime | None:
        """Return the earliest trade_date recorded for this instrument (current rows only).

        Returns ``None`` if no data exists. Together with ``latest_timestamp`` this lets the
        ingestion worker detect a backfill that reaches before the stored history.
        """
        row = self._store.conn.execute(
            """
            SELECT MIN(trade_date) FROM daily_prices
            WHERE instrument_id = ? AND known_to IS NULL
            """,
            [instrument_id],
        ).fetchone()

        if row is None or row[0] is None:
            return None
        d = row[0]
        return datetime(d.year, d.month, d.day, tzinfo=UTC)

    # ------------------------------------------------------------------
    # Extended: point-in-time read (not in Protocol, used by research layer)
    # ------------------------------------------------------------------

    def load_daily_as_of(
        self,
        instrument_id: str,
        start: date,
        end: date,
        known_at: datetime,
    ) -> list[Candle]:
        """Return bars as they were known at a specific system timestamp.

        Implements the ``get_daily(..., known_at=...)`` read pattern from
        DATABASE_SCHEMA §14.  Rows satisfy:
            known_from <= known_at < known_to   (or known_to IS NULL)
        """
        rows = self._store.conn.execute(
            """
            SELECT
                instrument_id, trade_date,
                open_raw, high_raw, low_raw, close_raw,
                volume_raw, primary_provider
            FROM daily_prices
            WHERE instrument_id = ?
              AND trade_date   >= ?
              AND trade_date   <= ?
              AND known_from   <= ?
              AND (known_to IS NULL OR known_to > ?)
            ORDER BY trade_date
            """,
            [instrument_id, start, end, known_at, known_at],
        ).fetchall()

        candles: list[Candle] = []
        for row in rows:
            iid, td, open_val, high_val, low_val, close_val, vol, prov = row
            candles.append(
                Candle(
                    instrument_id=iid,
                    timestamp=datetime(td.year, td.month, td.day, tzinfo=UTC),
                    timeframe=Timeframe.DAILY,
                    open=open_val,
                    high=high_val,
                    low=low_val,
                    close=close_val,
                    volume=vol,
                    provider=prov,
                )
            )
        return candles

    # ------------------------------------------------------------------
    # Adjusted prices (derived, rebuildable; DATABASE_SCHEMA section 14)
    # ------------------------------------------------------------------

    def save_adjusted_daily(self, rows: list[DailyPriceAdjustedRow]) -> int:
        """Persist derived adjusted bars, all-or-nothing.

        Keyed by ``(instrument_id, trade_date, adjustment_version, computed_from_snapshot_id)``.
        A different version or a different data snapshot never touches another's rows;
        re-saving the same version under the same snapshot refreshes its values and
        ``computed_at`` (idempotent rebuild). A row with no snapshot is stored under the
        explicit unfrozen marker ``LIVE``.

        Returns the number of rows written.
        """
        if not rows:
            return 0

        params = [
            [
                r.instrument_id,
                r.trade_date,
                r.open_adj,
                r.high_adj,
                r.low_adj,
                r.close_adj,
                r.volume_adj,
                r.adjustment_version,
                r.price_factor_applied,
                r.volume_factor_applied,
                r.computed_from_snapshot_id or LIVE_SNAPSHOT_ID,
                r.computed_at,
            ]
            for r in rows
        ]
        conn = self._store.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.executemany(
                """
                INSERT INTO daily_prices_adjusted (
                    instrument_id, trade_date,
                    open_adj, high_adj, low_adj, close_adj, volume_adj,
                    adjustment_version,
                    price_factor_applied, volume_factor_applied,
                    computed_from_snapshot_id, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    instrument_id, trade_date, adjustment_version, computed_from_snapshot_id
                ) DO UPDATE SET
                    open_adj = EXCLUDED.open_adj,
                    high_adj = EXCLUDED.high_adj,
                    low_adj = EXCLUDED.low_adj,
                    close_adj = EXCLUDED.close_adj,
                    volume_adj = EXCLUDED.volume_adj,
                    price_factor_applied = EXCLUDED.price_factor_applied,
                    volume_factor_applied = EXCLUDED.volume_factor_applied,
                    computed_at = EXCLUDED.computed_at
                """,
                params,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return len(rows)

    def load_adjusted_daily(
        self,
        instrument_id: str,
        start: date,
        end: date,
        adjustment_version: str | None = None,
        data_snapshot_id: str = LIVE_SNAPSHOT_ID,
    ) -> list[DailyPriceAdjustedRow]:
        """Load adjusted bars in [start, end] from one data snapshot (default: ``LIVE``).

        With no ``adjustment_version`` the instrument's *current* version within that
        snapshot is read (the most recently computed one, via
        ``daily_prices_adjusted_current``).
        """
        if adjustment_version is None:
            source, version_clause, extra = "daily_prices_adjusted_current", "", []
        else:
            source = "daily_prices_adjusted"
            version_clause = "AND adjustment_version = ?"
            extra = [adjustment_version]

        result = self._store.conn.execute(
            f"""
            SELECT instrument_id, trade_date,
                   open_adj, high_adj, low_adj, close_adj, volume_adj,
                   adjustment_version, price_factor_applied, volume_factor_applied,
                   computed_at, computed_from_snapshot_id
            FROM {source}
            WHERE instrument_id = ? AND trade_date >= ? AND trade_date <= ?
              AND computed_from_snapshot_id = ? {version_clause}
            ORDER BY trade_date
            """,  # noqa: S608 - source/clause are fixed literals above, values are bound
            [instrument_id, start, end, data_snapshot_id, *extra],
        ).fetchall()

        return [
            DailyPriceAdjustedRow(
                instrument_id=r[0],
                trade_date=r[1],
                open_adj=r[2],
                high_adj=r[3],
                low_adj=r[4],
                close_adj=r[5],
                volume_adj=r[6],
                adjustment_version=r[7],
                price_factor_applied=r[8],
                volume_factor_applied=r[9],
                computed_at=r[10],
                computed_from_snapshot_id=None if r[11] == LIVE_SNAPSHOT_ID else r[11],
            )
            for r in result
        ]

    def current_adjustment_version(
        self, instrument_id: str, data_snapshot_id: str = LIVE_SNAPSHOT_ID
    ) -> str | None:
        """The version downstream readers use for this instrument within a data snapshot.

        None if the instrument was never built under that snapshot.
        """
        row = self._store.conn.execute(
            """
            SELECT adjustment_version FROM daily_prices_adjusted_current
            WHERE instrument_id = ? AND computed_from_snapshot_id = ? LIMIT 1
            """,
            [instrument_id, data_snapshot_id],
        ).fetchone()
        return None if row is None else str(row[0])

    def load_priced_instrument_ids(self) -> list[str]:
        """Instruments that have at least one current raw daily bar, sorted."""
        rows = self._store.conn.execute(
            """
            SELECT DISTINCT instrument_id FROM daily_prices
            WHERE known_to IS NULL ORDER BY instrument_id
            """
        ).fetchall()
        return [str(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # Raw OHLCV helpers (append-only, no dedup logic needed here)
    # ------------------------------------------------------------------

    def save_raw_ohlcv(self, rows: list[RawOHLCVRow]) -> int:
        """Append raw provider candles to ``raw_ohlcv`` (insert-only, rule 2)."""
        inserted = 0
        for row in rows:
            self._store.conn.execute(
                """
                INSERT INTO raw_ohlcv (
                    provider, provider_instrument_id, instrument_id,
                    timestamp, interval,
                    open, high, low, close, volume, oi,
                    received_at, ingestion_run_id, source_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.provider,
                    row.provider_instrument_id,
                    row.instrument_id,
                    row.timestamp,
                    row.interval,
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                    row.volume,
                    row.oi,
                    row.received_at,
                    row.ingestion_run_id,
                    row.source_hash,
                ],
            )
            inserted += 1
        return inserted

    # ------------------------------------------------------------------
    # Ingestion run tracking
    # ------------------------------------------------------------------

    def save_ingestion_run(self, run: IngestionRunRow) -> None:
        """Insert or replace an ingestion run row."""
        self._store.conn.execute(
            """
            INSERT OR REPLACE INTO ingestion_runs (
                ingestion_run_id, provider, dataset,
                started_at, completed_at, status,
                requested_start, requested_end,
                records_received, records_written, records_rejected, error_count,
                config_hash, code_version, source_metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run.ingestion_run_id,
                run.provider,
                run.dataset,
                run.started_at,
                run.completed_at,
                run.status,
                run.requested_start,
                run.requested_end,
                run.records_received,
                run.records_written,
                run.records_rejected,
                run.error_count,
                run.config_hash,
                run.code_version,
                run.source_metadata_json,
            ],
        )

    def load_ingestion_run(self, run_id: str) -> IngestionRunRow | None:
        """Load a single ingestion run by ID, or None if not found."""
        row = self._store.conn.execute(
            """
            SELECT
                ingestion_run_id, provider, dataset,
                started_at, status,
                requested_start, requested_end,
                completed_at,
                records_received, records_written, records_rejected, error_count,
                config_hash, code_version, source_metadata_json
            FROM ingestion_runs WHERE ingestion_run_id = ?
            """,
            [run_id],
        ).fetchone()

        if row is None:
            return None

        return IngestionRunRow(
            ingestion_run_id=row[0],
            provider=row[1],
            dataset=row[2],
            started_at=row[3],
            status=row[4],
            requested_start=row[5],
            requested_end=row[6],
            completed_at=row[7],
            records_received=row[8] or 0,
            records_written=row[9] or 0,
            records_rejected=row[10] or 0,
            error_count=row[11] or 0,
            config_hash=row[12],
            code_version=row[13],
            source_metadata_json=row[14],
        )
