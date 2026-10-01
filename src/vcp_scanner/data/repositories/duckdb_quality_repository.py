"""DuckDB persistence and gate for ``data_quality_events`` (audit finding P0-2).

Two jobs:

* **Store** events raised by the detectors (completeness, corporate-action reconciliation,
  the gap safety net) so they survive the command that found them.
* **Gate** downstream consumers: ``blocked_instruments`` says which instruments have an
  unresolved, signal-blocking event that applies at an as-of date (and, for point-in-time
  runs, that was already known at ``known_at``).

Event lifecycle. Event ids are deterministic per condition, so re-detection updates the
existing row. ``sync_events`` reconciles one instrument's events of one kind with what a
detector currently sees: new conditions are opened, still-present ones are refreshed, and
conditions that have cleared are closed by the system (``resolved_by = 'SYSTEM'``) and may
reopen if they return. A human resolution (``resolve``) is final: the system never reopens
or overwrites it.

History (audit P1-2a, C5). Every change of an event's status or ``blocks_signal`` closes its
current interval in ``data_quality_event_history`` and opens a new one, so the point-in-time
gate (``known_at``) sees exactly the state each event had then, including a resolution that
was later reopened.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.events import (
    SYSTEM_RESOLVER,
    DataQualityEvent,
    EventSeverity,
    EventStatus,
)

_UPSERT = """
    INSERT INTO data_quality_events (
        event_id, instrument_id, trade_date, dataset, severity, blocks_signal, event_type,
        description, detected_at, status, context
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
    ON CONFLICT (event_id) DO UPDATE SET
        severity = EXCLUDED.severity,
        blocks_signal = EXCLUDED.blocks_signal,
        description = EXCLUDED.description,
        context = EXCLUDED.context,
        status = 'OPEN',
        resolved_at = NULL,
        resolved_by = NULL,
        resolution_note = NULL
    WHERE data_quality_events.resolved_by IS NULL
       OR data_quality_events.resolved_by = 'SYSTEM'
"""

_COLUMNS = (
    "event_id, instrument_id, trade_date, dataset, severity, blocks_signal, event_type, "
    "description, detected_at, status, resolved_at, resolved_by, resolution_note, context"
)


#: Kinds whose block is tied to stored reconciliation state, not to an operator's opinion.
_SUPERSEDE_ONLY = frozenset({DataQualityFlag.CORPORATE_ACTION_UNRESOLVED.value})


@dataclass(frozen=True, slots=True)
class SyncResult:
    """What ``sync_events`` changed for one instrument and kind."""

    opened: int  # conditions seen for the first time
    resolved: int  # open events closed because the condition cleared


class DuckDBDataQualityRepository:
    """Persistence plus the signal gate. ``known_at`` makes the gate point-in-time."""

    def __init__(
        self,
        store: DuckDBStore,
        *,
        known_at: datetime | None = None,
        block_lifetime_bars: int | None = None,
    ) -> None:
        self._store = store
        self._known_at = known_at
        # Audit P1-2: a dated block ends once the instrument has this many bars from the event
        # date to the as-of date (the bad bar has left every lookback). None = never ends.
        self._lifetime = block_lifetime_bars

    # ------------------------------------------------------------------ writes

    def sync_events(
        self,
        instrument_id: str,
        flag: DataQualityFlag,
        current: Sequence[DataQualityEvent],
        *,
        at: datetime,
    ) -> SyncResult:
        """Make the stored events for (instrument, flag) match ``current``.

        ``current`` is the complete set the detector sees now (empty means "all clear").
        ``at`` is injected by the caller; this method never reads the clock.
        """
        for event in current:
            if event.instrument_id != instrument_id or event.flag is not flag:
                raise ValueError(
                    f"event {event.event_id} does not belong to {instrument_id}/{flag}"
                )

        conn = self._store.conn
        ids = [e.event_id for e in current]
        before = self._states(instrument_id, flag)
        existing = set(before)
        rows = [
            (
                e.event_id,
                e.instrument_id,
                e.trade_date,
                e.dataset,
                e.severity.value,
                e.blocks_signal,
                e.flag.value,
                e.description,
                e.detected_at,
                json.dumps(e.context, sort_keys=True) if e.context else None,
            )
            for e in current
        ]
        conn.execute("BEGIN TRANSACTION")
        try:
            if rows:
                conn.executemany(_UPSERT, rows)
            closed = conn.execute(
                """
                UPDATE data_quality_events
                SET status = 'RESOLVED', resolved_at = ?, resolved_by = ?,
                    resolution_note = 'Condition no longer detected'
                WHERE instrument_id = ? AND event_type = ? AND status = 'OPEN'
                  AND NOT list_contains(CAST(? AS VARCHAR[]), event_id)
                RETURNING event_id
                """,
                [at, SYSTEM_RESOLVER, instrument_id, flag.value, ids],
            ).fetchall()
            after = self._states(instrument_id, flag)
            for event_id, state in after.items():
                if before.get(event_id) != state:
                    self._record(event_id, state, at)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return SyncResult(opened=len(set(ids) - existing), resolved=len(closed))

    def resolve(self, event_id: str, *, resolved_by: str, note: str, resolved_at: datetime) -> bool:
        """Record a human decision closing an OPEN event. Returns False if none was open.

        A name and a note are mandatory (audit trail, DATA_SPECIFICATION 18A), and the name
        may not be the reserved system resolver. Corporate-action conflicts cannot be closed
        this way (see ``_SUPERSEDE_ONLY``); a gap can, because a genuine gap is a human call.
        """
        who = resolved_by.strip()
        if not who or who.upper() == SYSTEM_RESOLVER:
            raise ValueError("resolved_by must name a person (not empty, not 'SYSTEM')")
        if not note.strip():
            raise ValueError("a resolution note is required")
        kind = self._store.conn.execute(
            "SELECT event_type FROM data_quality_events WHERE event_id = ? AND status = 'OPEN'",
            [event_id],
        ).fetchone()
        if kind is not None and kind[0] in _SUPERSEDE_ONLY:
            raise ValueError(
                f"{kind[0]} events cannot be closed by hand: they clear only when the corporate-"
                "action resolution is superseded (DATABASE_SCHEMA 17A). Closing one would let "
                "signals run on prices whose adjustment is still withheld."
            )
        row = self._store.conn.execute(
            """
            UPDATE data_quality_events
            SET status = 'RESOLVED', resolved_at = ?, resolved_by = ?, resolution_note = ?
            WHERE event_id = ? AND status = 'OPEN'
            RETURNING event_id
            """,
            [resolved_at, who, note.strip(), event_id],
        ).fetchone()
        if row is not None:
            blocks = self._store.conn.execute(
                "SELECT blocks_signal FROM data_quality_events WHERE event_id = ?", [event_id]
            ).fetchone()
            blocking = bool(blocks[0]) if blocks is not None else True
            self._record(event_id, ("RESOLVED", blocking, who), resolved_at)
        return row is not None

    def _states(
        self, instrument_id: str, flag: DataQualityFlag
    ) -> dict[str, tuple[str, bool, str | None]]:
        rows = self._store.conn.execute(
            "SELECT event_id, status, blocks_signal, resolved_by FROM data_quality_events"
            " WHERE instrument_id = ? AND event_type = ?",
            [instrument_id, flag.value],
        ).fetchall()
        return {str(r[0]): (str(r[1]), bool(r[2]), r[3]) for r in rows}

    def _record(self, event_id: str, state: tuple[str, bool, str | None], at: datetime) -> None:
        """Close the event's current history interval at ``at`` and open ``state`` (C5).

        A change stamped at or before the current interval's start (a re-run with an older
        knowledge time) replaces that interval instead, so intervals never overlap.
        """
        status, blocks, who = state
        conn = self._store.conn
        cur = conn.execute(
            "SELECT valid_from FROM data_quality_event_history"
            " WHERE event_id = ? AND valid_to IS NULL",
            [event_id],
        ).fetchone()
        if cur is not None and at <= cur[0]:
            conn.execute(
                "UPDATE data_quality_event_history SET status = ?, blocks_signal = ?,"
                " resolved_by = ? WHERE event_id = ? AND valid_to IS NULL",
                [status, blocks, who, event_id],
            )
            return
        conn.execute(
            "UPDATE data_quality_event_history SET valid_to = ?"
            " WHERE event_id = ? AND valid_to IS NULL",
            [at, event_id],
        )
        conn.execute(
            "INSERT INTO data_quality_event_history"
            " (event_id, status, blocks_signal, valid_from, valid_to, resolved_by)"
            " VALUES (?, ?, ?, ?, NULL, ?)",
            [event_id, status, blocks, at, who],
        )

    # ------------------------------------------------------------------ reads

    def load_events(
        self,
        *,
        instrument_ids: Sequence[str] | None = None,
        flag: DataQualityFlag | None = None,
        open_only: bool = False,
    ) -> list[DataQualityEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if instrument_ids is not None:
            clauses.append("list_contains(CAST(? AS VARCHAR[]), instrument_id)")
            params.append(list(instrument_ids))
        if flag is not None:
            clauses.append("event_type = ?")
            params.append(flag.value)
        if open_only:
            clauses.append("status = 'OPEN'")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._store.conn.execute(
            f"SELECT {_COLUMNS} FROM data_quality_events {where} "  # noqa: S608 - fixed literals
            "ORDER BY instrument_id, trade_date NULLS FIRST, event_id",
            params,
        ).fetchall()
        return [_to_event(r) for r in rows]

    def blocked_instruments(
        self,
        instrument_ids: Sequence[str],
        as_of_date: date,
        *,
        known_at: datetime | None = None,
    ) -> dict[str, tuple[str, ...]]:
        """Instruments among ``instrument_ids`` that may not emit signals at ``as_of_date``.

        Maps instrument id -> sorted distinct blocking flags. An event applies when its
        ``trade_date`` is NULL or on/before ``as_of_date``. Without a ``known_at`` (argument
        or constructor) only currently OPEN events count. With one, the gate answers as it
        would have then: the event must have been detected by ``known_at`` and not yet
        resolved at that time, so a later detection never blocks an earlier snapshot.
        With ``block_lifetime_bars`` a dated event also stops applying once the instrument
        has that many bars from its date to ``as_of_date`` (audit P1-2).
        """
        if not instrument_ids:
            return {}
        cutoff = known_at if known_at is not None else self._known_at
        params: list[Any] = [list(instrument_ids), as_of_date]
        if cutoff is None:
            time_clause = "AND e.blocks_signal AND e.status = 'OPEN'"
        else:
            # Audit P1-2a (C5): the state the event had at the cutoff, from its history.
            time_clause = """AND EXISTS (
                SELECT 1 FROM data_quality_event_history h
                WHERE h.event_id = e.event_id AND h.status = 'OPEN' AND h.blocks_signal
                  AND h.valid_from <= ? AND (h.valid_to IS NULL OR h.valid_to > ?))"""
            params += [cutoff, cutoff]
        lifetime_clause = ""
        if self._lifetime is not None:
            # Audit P1-2: the block lasts while fewer than ``lifetime`` of the instrument's
            # bars lie in [event date, as-of]. Once that many do, every lookback window ending
            # at the as-of date starts on or after the event bar, so none spans the bad jump.
            # Bars are read as known at ``cutoff`` when one is given (point-in-time).
            bar_time = (
                "AND p.known_to IS NULL"
                if cutoff is None
                else "AND p.known_from <= ? AND (p.known_to IS NULL OR p.known_to > ?)"
            )
            lifetime_clause = f"""
              AND (e.trade_date IS NULL OR (
                    SELECT count(*) FROM daily_prices p
                    WHERE p.instrument_id = e.instrument_id
                      AND p.trade_date >= e.trade_date AND p.trade_date <= ?
                      {bar_time}
                  ) < ?)"""
            params.append(as_of_date)
            if cutoff is not None:
                params += [cutoff, cutoff]
            params.append(self._lifetime)
        rows = self._store.conn.execute(
            f"""
            SELECT DISTINCT e.instrument_id, e.event_type
            FROM data_quality_events e
            WHERE list_contains(CAST(? AS VARCHAR[]), e.instrument_id)
              AND (e.trade_date IS NULL OR e.trade_date <= ?)
              {time_clause}
              {lifetime_clause}
            ORDER BY e.instrument_id, e.event_type
            """,  # noqa: S608 - clauses are fixed literals above; values are bound
            params,
        ).fetchall()
        blocked: dict[str, list[str]] = {}
        for iid, flag in rows:
            blocked.setdefault(iid, []).append(flag)
        return {iid: tuple(flags) for iid, flags in blocked.items()}


def _to_event(row: tuple[Any, ...]) -> DataQualityEvent:
    (event_id, iid, trade_date, dataset, severity, blocks, flag, description, detected_at) = row[:9]
    status, resolved_at, resolved_by, note, context = row[9:]
    return DataQualityEvent(
        event_id=event_id,
        instrument_id=iid,
        flag=DataQualityFlag(flag),
        severity=EventSeverity(severity),
        detected_at=detected_at,
        description=description,
        context=json.loads(context) if context else None,
        trade_date=trade_date,
        blocks_signal=bool(blocks),
        dataset=dataset,
        status=EventStatus(status),
        resolved_at=resolved_at,
        resolved_by=resolved_by,
        resolution_note=note,
    )
