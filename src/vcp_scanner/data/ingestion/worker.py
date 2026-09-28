"""Ingestion worker — orchestrates provider → storage pipeline (PROJECT_DESIGN §8).

Rules obeyed (AGENTS.md):
- Typed against ``MarketDataProvider`` Protocol only — no SDK imports (rule 3).
- No datetime.now() in research paths; ``ingestion_time`` param is explicit (rule 1).
- Raw data goes to raw_ohlcv first; canonical daily_prices is derived from it (rule 2).
- Chunk size comes from ``ProviderCapabilities``, not hard-coded (rule 7).
- Records RUNNING → SUCCESS / PARTIAL / FAILED in ingestion_runs (DATABASE_SCHEMA §11).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from vcp_scanner.data.schema import (
    IngestionRunRow,
    RawOHLCVRow,
    candle_source_hash,
    validate_ohlc,
)

if TYPE_CHECKING:
    from vcp_scanner.data.providers.base import MarketDataProvider
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )
    from vcp_scanner.domain.market import Candle, Instrument

logger = logging.getLogger(__name__)


def _date_chunks(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    """Split [start, end] into consecutive chunks of at most ``max_days`` days.

    No look-ahead: purely calendar-day arithmetic.  The caller decides how to
    align with the trading calendar.
    """
    chunks: list[tuple[date, date]] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=max_days - 1), end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks


class IngestionWorker:
    """Orchestrates market-data ingestion from a provider into local storage.

    Dependency-injected with abstract interfaces — no broker SDK or DuckDB
    imports in strategy/pattern code that uses this class.

    Usage::

        worker = IngestionWorker(provider=kite_provider, repository=duck_repo)
        result = worker.ingest_instrument(instrument, start, end)
    """

    def __init__(
        self,
        provider: "MarketDataProvider",
        repository: "DuckDBMarketDataRepository",
        code_version: str = "dev",
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._code_version = code_version

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_instrument(
        self,
        instrument: "Instrument",
        start: date,
        end: date,
        *,
        ingestion_time: datetime | None = None,
        force: bool = False,
    ) -> IngestionRunRow:
        """Ingest daily OHLCV for one instrument from ``start`` to ``end``.

        Steps:
        1. Record RUNNING ingestion_run.
        2. Determine the effective start date:
           - If ``force=False``, skip dates already covered locally (idempotent).
        3. Chunk the remaining date range by provider's max request window.
        4. For each chunk: fetch → validate → save raw → save canonical.
        5. Update ingestion_run to SUCCESS / PARTIAL / FAILED.

        Args:
            instrument: The canonical instrument to ingest data for.
            start: Requested start date (inclusive).
            end: Requested end date (inclusive).
            ingestion_time: Explicit system timestamp for bitemporal known_from.
                            Defaults to UTC now at call time (acceptable here
                            because this IS the ingestion timestamp, not a
                            research/analysis timestamp).
            force: If True, re-fetch the full range even if local data exists.

        Returns:
            The completed IngestionRunRow with final status and counts.
        """
        now_utc: datetime = ingestion_time or datetime.now(UTC)
        run_id = str(uuid.uuid4())

        run = IngestionRunRow(
            ingestion_run_id=run_id,
            provider=self._provider.__class__.__name__,
            dataset="daily_ohlcv",
            started_at=now_utc,
            status="RUNNING",
            requested_start=start,
            requested_end=end,
            code_version=self._code_version,
        )
        self._repo.save_ingestion_run(run)

        records_received = 0
        records_written = 0
        records_rejected = 0
        error_count = 0

        try:
            effective_start = self._effective_start(instrument, start, force)
            if effective_start is None:
                # All dates in range already covered; nothing to do.
                logger.info(
                    "Ingest skipped: %s [%s, %s] already fully covered locally.",
                    instrument.instrument_id,
                    start,
                    end,
                )
                run = replace(
                    run,
                    status="SUCCESS",
                    completed_at=now_utc,
                    records_received=0,
                    records_written=0,
                    records_rejected=0,
                    error_count=0,
                )
                self._repo.save_ingestion_run(run)
                return run

            caps = self._provider.get_capabilities()
            chunk_size = caps.daily_history_max_request_days

            chunks = _date_chunks(effective_start, end, chunk_size)
            logger.info(
                "Ingesting %s from %s to %s in %d chunk(s) (max %d days).",
                instrument.instrument_id,
                effective_start,
                end,
                len(chunks),
                chunk_size,
            )

            for chunk_start, chunk_end in chunks:
                try:
                    candles: list["Candle"] = self._provider.get_historical_daily(
                        instrument, chunk_start, chunk_end
                    )
                    records_received += len(candles)

                    raw_rows, valid_candles, rejected = self._validate_and_build_raw(
                        candles, run_id, now_utc
                    )
                    records_rejected += rejected

                    # Append raw (immutable, always)
                    self._repo.save_raw_ohlcv(raw_rows)

                    # Upsert canonical (bitemporal)
                    written = self._repo.save_daily(valid_candles)
                    records_written += written

                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Error ingesting chunk [%s, %s] for %s",
                        chunk_start,
                        chunk_end,
                        instrument.instrument_id,
                    )
                    error_count += 1

            status = "SUCCESS" if error_count == 0 else "PARTIAL"
            if records_received == 0 and error_count > 0:
                status = "FAILED"

        except Exception:  # noqa: BLE001
            logger.exception("Fatal error ingesting %s", instrument.instrument_id)
            error_count += 1
            status = "FAILED"
            records_received = records_received
            records_written = records_written
            records_rejected = records_rejected

        completed_run = replace(
            run,
            status=status,
            completed_at=datetime.now(UTC),
            records_received=records_received,
            records_written=records_written,
            records_rejected=records_rejected,
            error_count=error_count,
        )
        self._repo.save_ingestion_run(completed_run)
        logger.info(
            "Ingest complete: %s status=%s received=%d written=%d rejected=%d errors=%d",
            instrument.instrument_id,
            status,
            records_received,
            records_written,
            records_rejected,
            error_count,
        )
        return completed_run

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _effective_start(
        self,
        instrument: "Instrument",
        requested_start: date,
        force: bool,
    ) -> date | None:
        """Return the actual start date after checking local coverage.

        If ``force=True`` return ``requested_start``.
        If we have data up to ``end`` already, return ``None`` (skip).
        Otherwise return the day after the latest stored date.
        """
        if force:
            return requested_start

        latest_ts = self._repo.latest_timestamp(instrument.instrument_id)
        if latest_ts is None:
            return requested_start

        latest_date = latest_ts.date()
        # Shift start to day after last stored date
        next_needed = latest_date + timedelta(days=1)
        if next_needed > date.today():
            return None  # fully up to date
        return max(next_needed, requested_start)

    def _validate_and_build_raw(
        self,
        candles: list["Candle"],
        run_id: str,
        received_at: datetime,
    ) -> tuple[list[RawOHLCVRow], list["Candle"], int]:
        """Validate OHLC and build raw storage rows.

        Returns:
            (raw_rows, valid_candles, rejected_count)
        """
        raw_rows: list[RawOHLCVRow] = []
        valid_candles: list["Candle"] = []
        rejected = 0

        for candle in candles:
            result = validate_ohlc(
                candle.open, candle.high, candle.low, candle.close, candle.volume
            )
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

            # Always persist raw (even invalid — preserved for audit).
            raw_rows.append(
                RawOHLCVRow(
                    provider=candle.provider,
                    provider_instrument_id=candle.instrument_id,  # use internal ID
                    instrument_id=candle.instrument_id,
                    timestamp=candle.timestamp,
                    interval=candle.timeframe,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    oi=None,
                    received_at=received_at,
                    ingestion_run_id=run_id,
                    source_hash=src_hash,
                )
            )

            if result.is_valid:
                valid_candles.append(candle)
            else:
                logger.warning(
                    "Rejected candle %s %s: %s",
                    candle.instrument_id,
                    candle.timestamp.date(),
                    result.reason,
                )
                rejected += 1

        return raw_rows, valid_candles, rejected
