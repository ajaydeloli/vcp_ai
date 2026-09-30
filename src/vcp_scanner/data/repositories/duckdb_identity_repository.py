"""DuckDB persistence for bhavcopy identity (audit step 2.2).

Tables: ``instrument_identifier_history`` (symbol/ISIN periods), ``daily_series`` (per-day
row -> instrument mapping) and new, inactive rows in ``instruments`` for names that no longer
trade (delisted) or are not yet in the security master.
"""

from __future__ import annotations

from datetime import date, datetime

from vcp_scanner.data.ingestion.bhavcopy_identity import DayIdentity, IdentityState, SeedInstrument
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.market import IdentifierPeriod

_HISTORY_INSERT = (
    "instrument_id",
    "symbol",
    "isin",
    "valid_from",
    "valid_to",
    "change_reason",
    "source",
    "recorded_at",
)
_SERIES_INSERT = ("trade_date", "symbol", "series", "instrument_id", "isin", "recorded_at")
_PERIOD_COLUMNS = "instrument_id, symbol, isin, valid_from, valid_to, change_reason, source"


def _period(row: tuple[object, ...]) -> IdentifierPeriod:
    iid, symbol, isin, valid_from, valid_to, reason, source = row
    assert isinstance(valid_from, date)
    assert valid_to is None or isinstance(valid_to, date)
    return IdentifierPeriod(
        str(iid), str(symbol), str(isin), valid_from, valid_to, str(reason), str(source)
    )


class DuckDBIdentityRepository:
    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def load_state(self) -> IdentityState:
        conn = self._store.conn
        state = IdentityState()
        for row in conn.execute(
            f"SELECT {_PERIOD_COLUMNS} FROM instrument_identifier_history ORDER BY valid_from"
        ).fetchall():
            period = _period(row)
            state.isin_owner.setdefault(period.isin, period.instrument_id)
            state.taken_ids.add(period.instrument_id)
            if period.valid_to is None:
                state.open_periods[period.instrument_id] = period
        for iid, symbol, isin in conn.execute(
            "SELECT instrument_id, symbol, isin FROM instruments ORDER BY instrument_id"
        ).fetchall():
            iid = str(iid)
            if isin:
                state.isin_owner.setdefault(str(isin), iid)
            if iid not in state.taken_ids:  # no history yet: a seed
                state.seeds[str(symbol)] = SeedInstrument(
                    iid, str(symbol), None if isin is None else str(isin)
                )
            state.taken_ids.add(iid)
        last = conn.execute("SELECT max(trade_date) FROM daily_series").fetchone()
        state.last_resolved = last[0] if last else None
        return state

    def load_day_mapping(self, trade_date: date) -> dict[tuple[str, str], str]:
        rows = self._store.conn.execute(
            "SELECT symbol, series, instrument_id FROM daily_series WHERE trade_date = ?",
            [trade_date],
        ).fetchall()
        return {(str(s), str(ser)): str(iid) for s, ser, iid in rows}

    def load_periods(self, instrument_id: str) -> list[IdentifierPeriod]:
        rows = self._store.conn.execute(
            f"SELECT {_PERIOD_COLUMNS} FROM instrument_identifier_history"
            " WHERE instrument_id = ? ORDER BY valid_from",
            [instrument_id],
        ).fetchall()
        return [_period(r) for r in rows]

    def apply(self, day: DayIdentity, *, recorded_at: datetime) -> None:
        """Persist one resolved day atomically. A replayed day writes nothing."""
        if day.replayed:
            return
        conn = self._store.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            # Inactive until the security master lists it (sm_worker activates it then).
            self._store.insert_rows(
                "instruments",
                (
                    "instrument_id",
                    "isin",
                    "exchange",
                    "symbol",
                    "is_active",
                    "created_at",
                    "updated_at",
                ),
                [
                    [i.instrument_id, i.isin, "NSE", i.symbol, False, recorded_at, recorded_at]
                    for i in day.new_instruments
                ],
                ignore_conflicts=True,
            )
            for p in day.closed:
                conn.execute(
                    "UPDATE instrument_identifier_history SET valid_to = ?, recorded_at = ?"
                    " WHERE instrument_id = ? AND valid_from = ?",
                    [p.valid_to, recorded_at, p.instrument_id, p.valid_from],
                )
            self._store.insert_rows(
                "instrument_identifier_history",
                _HISTORY_INSERT,
                [
                    [
                        p.instrument_id,
                        p.symbol,
                        p.isin,
                        p.valid_from,
                        p.valid_to,
                        p.change_reason,
                        p.source,
                        recorded_at,
                    ]
                    for p in day.opened
                ],
            )
            self._store.insert_rows(
                "daily_series",
                _SERIES_INSERT,
                [
                    [r.trade_date, r.symbol, r.series, r.instrument_id, r.isin, recorded_at]
                    for r in day.daily_series
                ],
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
