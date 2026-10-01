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
from dataclasses import dataclass, field
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
from vcp_scanner.domain.market import FINAL_PRICE_SOURCE, Candle
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.infrastructure.clock import Clock, utc_now

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FinalDailyBar:
    """One session's final raw bar for an instrument, from the NSE bhavcopy."""

    instrument_id: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    series: str


@dataclass(frozen=True, slots=True)
class FinalDailySave:
    written: int = 0  # new current bars
    unchanged: int = 0  # identical bar already current
    superseded: dict[str, int] = field(default_factory=dict)  # closed bars by provider


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
        *,
        include_provisional: bool = False,
    ) -> list[Candle]:
        """Return current canonical daily bars in [start, end] (inclusive).

        Only rows with ``known_to IS NULL`` are returned — i.e. the current
        truth for each trade_date.  Superseded corrections are invisible here;
        use ``load_daily_as_of`` for point-in-time reads. PROVISIONAL bars (today's Kite bar
        before the bhavcopy is published, audit step 2.5) are left out unless asked for.
        """
        rows = self._store.conn.execute(
            """
            SELECT
                instrument_id, trade_date,
                open_raw, high_raw, low_raw, close_raw,
                volume_raw, primary_provider, known_from
            FROM daily_prices
            WHERE instrument_id = ?
              AND trade_date   >= ?
              AND trade_date   <= ?
              AND known_to IS NULL
              AND (? OR data_status <> 'PROVISIONAL')
            ORDER BY trade_date
            """,
            [instrument_id, start, end, include_provisional],
        ).fetchall()

        candles: list[Candle] = []
        for row in rows:
            iid, td, open_val, high_val, low_val, close_val, vol, prov, known_from = row
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
                    # Fetch time of this version: tells the adjustment engine which actions a
                    # provider-adjusted source had already applied (audit P0-1).
                    ingested_at=known_from,
                )
            )
        return candles

    def save_daily(self, candles: list[Candle], *, data_status: str = "OK") -> int:
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

        ``data_status`` is stored on every inserted row; ``PROVISIONAL`` marks today's Kite bar
        taken before the NSE bhavcopy is published (audit step 2.5). The bhavcopy bar later
        supersedes it, and no non-bhavcopy bar ever supersedes a bhavcopy bar.
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
                SELECT source_hash, primary_provider FROM daily_prices
                WHERE instrument_id = ? AND trade_date = ? AND known_to IS NULL
                """,
                [candle.instrument_id, trade_date],
            ).fetchone()

            if existing is not None and existing[0] == src_hash:
                # Exact duplicate — idempotent, skip silently.
                continue
            if (
                existing is not None
                and existing[1] == FINAL_PRICE_SOURCE
                and candle.provider != FINAL_PRICE_SOURCE
            ):
                # The bhavcopy bar for a session is final (audit step 2.3): never superseded
                # by another provider's bar for the same date.
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
                    data_status,
                    candle.provider_request_id or "unknown",
                    src_hash,
                    known_from,
                ],
            )
            inserted += 1

        return inserted

    def save_final_daily(
        self, rows: list[FinalDailyBar], *, known_from: datetime, source_run_id: str
    ) -> FinalDailySave:
        """Write one batch of final (bhavcopy) bars set-based (audit step 2.3).

        Per (instrument_id, trade_date): an identical current bar (same source hash) is kept;
        any other current bar, from any provider, is closed at ``known_from`` and the new bar
        becomes current. Bars must be valid (the parser already ran ``validate_ohlc``) and
        unique per (instrument_id, trade_date).
        """
        if not rows:
            return FinalDailySave()
        columns = (
            "instrument_id",
            "trade_date",
            "open_raw",
            "high_raw",
            "low_raw",
            "close_raw",
            "volume_raw",
            "selection_reason",
            "source_hash",
        )
        payload = [
            (
                r.instrument_id,
                r.trade_date,
                r.open,
                r.high,
                r.low,
                r.close,
                r.volume,
                f"series={r.series}",
                candle_source_hash(
                    instrument_id=r.instrument_id,
                    timestamp_iso=datetime(
                        r.trade_date.year, r.trade_date.month, r.trade_date.day, tzinfo=UTC
                    ).isoformat(),
                    timeframe=str(Timeframe.DAILY),
                    open_=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    volume=r.volume,
                    provider=FINAL_PRICE_SOURCE,
                ),
            )
            for r in rows
        ]
        conn = self._store.conn
        with self._store.registered("_final_daily", columns, payload) as new:
            conn.execute("BEGIN TRANSACTION")
            try:
                superseded = {
                    str(provider): int(n)
                    for provider, n in conn.execute(
                        f"""
                        SELECT d.primary_provider, count(*)
                        FROM daily_prices d JOIN {new} n USING (instrument_id, trade_date)
                        WHERE d.known_to IS NULL AND d.source_hash <> n.source_hash
                        GROUP BY 1
                        """
                    ).fetchall()
                }
                conn.execute(
                    f"""
                    UPDATE daily_prices SET known_to = ?
                    FROM {new} n
                    WHERE daily_prices.instrument_id = n.instrument_id
                      AND daily_prices.trade_date = n.trade_date
                      AND daily_prices.known_to IS NULL
                      AND daily_prices.source_hash <> n.source_hash
                    """,
                    [known_from],
                )
                inserted = conn.execute(
                    f"""
                    INSERT INTO daily_prices (
                        instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw,
                        volume_raw, primary_provider, selection_reason, data_status,
                        source_run_id, source_hash, known_from, known_to
                    )
                    SELECT n.instrument_id, n.trade_date, n.open_raw, n.high_raw, n.low_raw,
                           n.close_raw, n.volume_raw, ?, n.selection_reason, 'OK', ?,
                           n.source_hash, ?, NULL
                    FROM {new} n
                    WHERE NOT EXISTS (
                        SELECT 1 FROM daily_prices d
                        WHERE d.instrument_id = n.instrument_id
                          AND d.trade_date = n.trade_date
                          AND d.known_to IS NULL
                    )
                    """,
                    [FINAL_PRICE_SOURCE, source_run_id, known_from],
                ).fetchone()
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        written = int(inserted[0]) if inserted else 0
        return FinalDailySave(written=written, unchanged=len(rows) - written, superseded=superseded)

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
        *,
        include_provisional: bool = False,
    ) -> list[Candle]:
        """Return bars as they were known at a specific system timestamp.

        PROVISIONAL bars are left out unless ``include_provisional`` (audit step 2.5).

        Implements the ``get_daily(..., known_at=...)`` read pattern from
        DATABASE_SCHEMA §14.  Rows satisfy:
            known_from <= known_at < known_to   (or known_to IS NULL)
        """
        rows = self._store.conn.execute(
            """
            SELECT
                instrument_id, trade_date,
                open_raw, high_raw, low_raw, close_raw,
                volume_raw, primary_provider, known_from
            FROM daily_prices
            WHERE instrument_id = ?
              AND trade_date   >= ?
              AND trade_date   <= ?
              AND known_from   <= ?
              AND (known_to IS NULL OR known_to > ?)
              AND (? OR data_status <> 'PROVISIONAL')
            ORDER BY trade_date
            """,
            [instrument_id, start, end, known_at, known_at, include_provisional],
        ).fetchall()

        candles: list[Candle] = []
        for row in rows:
            iid, td, open_val, high_val, low_val, close_val, vol, prov, known_from = row
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
                    # Fetch time of this version: tells the adjustment engine which actions a
                    # provider-adjusted source had already applied (audit P0-1).
                    ingested_at=known_from,
                )
            )
        return candles

    # ------------------------------------------------------------------
    # Adjusted prices (derived, rebuildable; DATABASE_SCHEMA section 14)
    # ------------------------------------------------------------------

    def save_adjusted_daily(
        self, rows: list[DailyPriceAdjustedRow], *, prune_missing: bool = False
    ) -> int:
        """Persist derived adjusted bars, all-or-nothing.

        Keyed by ``(instrument_id, trade_date, adjustment_version, computed_from_snapshot_id)``.
        A different version or a different data snapshot never touches another's rows;
        re-saving the same version under the same snapshot refreshes its values and
        ``computed_at`` (idempotent rebuild). A row with no snapshot is stored under the
        explicit unfrozen marker ``LIVE``.

        ``prune_missing`` (full rebuilds): rows of the same (instrument, version, snapshot)
        whose trade date is not in ``rows`` are deleted, so a bar that no longer feeds the
        build (e.g. a PROVISIONAL bar left out, audit step 2.5) does not linger.

        Returns the number of rows written.
        """
        if not rows:
            return 0

        columns = (
            "instrument_id",
            "trade_date",
            "open_adj",
            "high_adj",
            "low_adj",
            "close_adj",
            "volume_adj",
            "adjustment_version",
            "price_factor_applied",
            "volume_factor_applied",
            "computed_from_snapshot_id",
            "computed_at",
        )
        # Last row wins per key, as the former row-by-row upsert did.
        by_key: dict[tuple[object, ...], tuple[object, ...]] = {}
        for r in rows:
            snapshot = r.computed_from_snapshot_id or LIVE_SNAPSHOT_ID
            by_key[(r.instrument_id, r.trade_date, r.adjustment_version, snapshot)] = (
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
                snapshot,
                r.computed_at,
            )
        conn = self._store.conn
        cols = ", ".join(columns)
        # Set-based through Arrow (audit step 2.3, D3): executemany upserted row by row.
        with self._store.registered("_adjusted_rows", columns, list(by_key.values())) as view:
            conn.execute("BEGIN TRANSACTION")
            try:
                if prune_missing:
                    conn.execute(
                        f"""
                        DELETE FROM daily_prices_adjusted a
                        WHERE EXISTS (
                            SELECT 1 FROM {view} v
                            WHERE v.instrument_id = a.instrument_id
                              AND v.adjustment_version = a.adjustment_version
                              AND v.computed_from_snapshot_id = a.computed_from_snapshot_id
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM {view} v
                            WHERE v.instrument_id = a.instrument_id
                              AND v.adjustment_version = a.adjustment_version
                              AND v.computed_from_snapshot_id = a.computed_from_snapshot_id
                              AND v.trade_date = a.trade_date
                        )
                        """
                    )
                conn.execute(
                    f"""
                    INSERT INTO daily_prices_adjusted ({cols})
                    SELECT {cols} FROM {view}
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
                    """
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
