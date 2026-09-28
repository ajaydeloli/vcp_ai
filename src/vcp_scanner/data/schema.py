"""Canonical row schemas for the data layer (DATABASE_SCHEMA §11–14).

These are NOT domain objects — they model database rows with bitemporal
provenance columns. Strategy code should never import these directly;
it operates on domain objects (Candle, Instrument) via repository interfaces.

Rules obeyed (AGENTS.md):
- known_from / known_to implement bitemporal append-only pattern (rule 2)
- No datetime.now() here — timestamps are injected by callers (rule 1)
- Missing is NULL, not 0 — optional fields use None (rule 4)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class IngestionRunRow:
    """One ingestion operation (DATABASE_SCHEMA §11 ``ingestion_runs``)."""

    ingestion_run_id: str  # UUID
    provider: str
    dataset: str  # e.g. "daily_ohlcv"
    started_at: datetime
    status: str  # RUNNING | SUCCESS | PARTIAL | FAILED
    requested_start: date | None = None
    requested_end: date | None = None
    completed_at: datetime | None = None
    records_received: int = 0
    records_written: int = 0
    records_rejected: int = 0
    error_count: int = 0
    config_hash: str | None = None
    code_version: str | None = None
    source_metadata_json: str | None = None


@dataclass(frozen=True, slots=True)
class RawOHLCVRow:
    """Immutable provider-level candle (DATABASE_SCHEMA §12 ``raw_ohlcv``).

    Raw means exactly-as-received. No adjusted prices here.
    """

    provider: str
    provider_instrument_id: str
    instrument_id: str
    timestamp: datetime  # UTC midnight for daily bars
    interval: str  # "1d", "5m", etc.
    open: float
    high: float
    low: float
    close: float
    volume: int | None  # None = provider did not supply a volume
    oi: float | None  # open interest — optional
    received_at: datetime
    ingestion_run_id: str
    source_hash: str  # SHA-256 of the serialised raw record


@dataclass(frozen=True, slots=True)
class DailyPriceRow:
    """Bitemporal canonical daily price (DATABASE_SCHEMA §14 ``daily_prices``).

    Rows are NEVER updated. A correction creates a new row with a later
    ``known_from`` and closes the previous row by setting its ``known_to``.
    ``known_to IS NULL`` means the row is the current truth.
    """

    instrument_id: str
    trade_date: date
    open_raw: float
    high_raw: float
    low_raw: float
    close_raw: float
    volume_raw: int | None
    primary_provider: str
    source_run_id: str
    source_hash: str
    known_from: datetime  # system time this row became current
    known_to: datetime | None = None  # NULL = currently active
    selection_reason: str | None = None
    data_status: str = "OK"  # OK | SUSPECT | REJECTED


@dataclass(frozen=True, slots=True)
class DailyPriceAdjustedRow:
    """Derived adjusted prices (DATABASE_SCHEMA §14 ``daily_prices_adjusted``).

    Rebuildable from raw + corporate_action_adjustments.
    Primary key: (instrument_id, trade_date, adjustment_version).
    """

    instrument_id: str
    trade_date: date
    open_adj: float
    high_adj: float
    low_adj: float
    close_adj: float
    volume_adj: float | None
    adjustment_version: str
    price_factor_applied: Decimal
    volume_factor_applied: Decimal
    computed_at: datetime
    computed_from_snapshot_id: str | None = None


# ---------------------------------------------------------------------------
# OHLC integrity check — used by the ingestion worker before any insert.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OHLCValidationResult:
    """Outcome of an OHLC sanity check (DATA_SPECIFICATION §23)."""

    is_valid: bool
    reason: str | None = None
    rejected_fields: list[str] = field(default_factory=list)


def validate_ohlc(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int | None,
) -> OHLCValidationResult:
    """Return OHLCValidationResult for a single bar (DATA_SPECIFICATION §23).

    Rejects:
        low > open, low > close, low > high, open > high, close > high,
        volume < 0 (None is permitted — provider may omit volume).
    """
    failures: list[str] = []

    if low > open_:
        failures.append("low > open")
    if low > close:
        failures.append("low > close")
    if low > high:
        failures.append("low > high")
    if open_ > high:
        failures.append("open > high")
    if close > high:
        failures.append("close > high")
    if volume is not None and volume < 0:
        failures.append("volume < 0")

    if failures:
        return OHLCValidationResult(
            is_valid=False,
            reason="; ".join(failures),
            rejected_fields=failures,
        )
    return OHLCValidationResult(is_valid=True)


def candle_source_hash(
    instrument_id: str,
    timestamp_iso: str,
    timeframe: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int | None,
    provider: str,
) -> str:
    """Deterministic SHA-256 for a candle's core fields (dedup / lineage).

    Accepts plain values rather than the Candle domain object to avoid a
    circular import between schema and domain layers.
    """
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    payload = json.dumps(
        {
            "instrument_id": instrument_id,
            "timestamp": timestamp_iso,
            "timeframe": timeframe,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "provider": provider,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
