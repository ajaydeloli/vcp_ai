"""Parquet archival engine.

Exports DuckDB tables to partitioned Parquet files for cold storage
(PROJECT_DESIGN section 11, DATABASE_SCHEMA section 3).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class ParquetArchiver:
    """Exports raw and canonical tables to partitioned Parquet directories."""

    def __init__(
        self,
        store: "DuckDBStore",
        raw_dir: str | Path,
        canonical_dir: str | Path,
    ) -> None:
        self._store = store
        self._raw_dir = Path(raw_dir)
        self._canonical_dir = Path(canonical_dir)

    def archive_raw_ohlcv(self) -> None:
        """Export raw_ohlcv partitioned by provider and instrument."""
        target = self._raw_dir / "raw_ohlcv"
        target.mkdir(parents=True, exist_ok=True)

        logger.info("Archiving raw_ohlcv to %s", target)
        self._store.conn.execute(
            f"""
            COPY (SELECT * FROM raw_ohlcv) 
            TO '{target}' 
            (FORMAT PARQUET, PARTITION_BY (provider, instrument_id), OVERWRITE_OR_IGNORE TRUE)
            """
        )

    def archive_daily_prices(self) -> None:
        """Export current canonical daily_prices partitioned by instrument."""
        target = self._canonical_dir / "daily_prices"
        target.mkdir(parents=True, exist_ok=True)

        logger.info("Archiving current daily_prices to %s", target)
        self._store.conn.execute(
            f"""
            COPY (SELECT * FROM daily_prices WHERE known_to IS NULL) 
            TO '{target}' 
            (FORMAT PARQUET, PARTITION_BY (instrument_id), OVERWRITE_OR_IGNORE TRUE)
            """
        )
