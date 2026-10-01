"""Daily-bar completeness checks (audit finding P0-4).

The repository has no published exchange calendar, so the trading calendar is *observed*
from the data itself: a date is a market session when at least ``min_breadth`` of the
instruments that were trading around it (first bar <= date <= last bar) have a bar on it.
Weekends and exchange holidays therefore drop out with no hand-maintained holiday list,
and extra sessions (Muhurat, special Saturday sessions) are picked up automatically.

An instrument is *incomplete* when it lacks a bar on a market session between its first
stored bar and the end of the checked window. That covers interior holes, which the
ingestion worker's head/tail coverage logic cannot see, and a stale tail.

Limits, stated plainly:

* Needs a cross-section. With fewer than ``min_active_instruments`` trading instruments the
  result is UNVERIFIED, never COMPLETE.
* A date on which only a minority of instruments have bars is reported as *suspect*
  (partial provider outage or glitch) and is not treated as a session, so it cannot be
  repaired automatically.
* A date with no bar for any instrument is invisible (holiday or total outage); the
  ingestion run statuses are what surface a total outage.
* A session whose NSE bhavcopy settled OK lists every stock that traded that day. If the
  stock has no bar on such a day it did not trade on NSE (suspension, trade-to-trade gap):
  that is an absence, not missing data, and it is not reported here (audit P1-2b, C6).
  Long absences are handled by ``TRADING_ABSENCE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from vcp_scanner.config.models import CompletenessConfig
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity, make_event_id
from vcp_scanner.infrastructure.clock import Clock, utc_now

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore


class CompletenessStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class SessionScan:
    """Market sessions observed in a date window."""

    sessions: tuple[date, ...]
    #: (date, instruments with a bar, instruments trading) for low-breadth weekdays.
    suspect_dates: tuple[tuple[date, int, int], ...]

    @property
    def verified(self) -> bool:
        return bool(self.sessions)


@dataclass(frozen=True, slots=True)
class CompletenessReport:
    instrument_id: str
    start: date
    end: date
    status: CompletenessStatus
    expected_sessions: int = 0
    missing_sessions: tuple[date, ...] = ()
    #: Consecutive missing sessions merged into inclusive [first, last] ranges.
    missing_ranges: tuple[tuple[date, date], ...] = ()
    suspect_dates: tuple[date, ...] = ()
    detail: str = ""

    def to_events(self, detected_at: datetime) -> list[DataQualityEvent]:
        """Data-quality events for this report (empty unless INCOMPLETE).

        One event per instrument, keyed by the first missing session. It blocks signals from
        that session on: every moving average, high/low and volume window spanning a hole is
        computed over the wrong bars. It clears when the hole is filled (``sync_events``).
        """
        if self.status is not CompletenessStatus.INCOMPLETE:
            return []
        first, last = self.missing_sessions[0], self.missing_sessions[-1]
        return [
            DataQualityEvent(
                event_id=make_event_id(
                    DataQualityFlag.MISSING_CANDLES, self.instrument_id, first.isoformat()
                ),
                instrument_id=self.instrument_id,
                flag=DataQualityFlag.MISSING_CANDLES,
                severity=EventSeverity.HIGH,
                detected_at=detected_at,
                trade_date=first,
                blocks_signal=True,
                dataset="daily_prices",
                description=(
                    f"{len(self.missing_sessions)} of {self.expected_sessions} market sessions "
                    f"have no daily bar ({first} to {last})"
                ),
                context={
                    "missing_sessions": len(self.missing_sessions),
                    "expected_sessions": self.expected_sessions,
                    "first_missing": first.isoformat(),
                    "last_missing": last.isoformat(),
                },
            )
        ]


def _merge_ranges(sessions: tuple[date, ...], missing: list[date]) -> tuple[tuple[date, date], ...]:
    """Merge missing dates that are neighbours in the session list into [first, last] ranges."""
    position = {d: i for i, d in enumerate(sessions)}
    ranges: list[tuple[date, date]] = []
    run_start = prev = missing[0]
    for d in missing[1:]:
        if position[d] == position[prev] + 1:
            prev = d
            continue
        ranges.append((run_start, prev))
        run_start = prev = d
    ranges.append((run_start, prev))
    return tuple(ranges)


class CompletenessChecker:
    """Checks stored daily bars against the observed market-session calendar."""

    def __init__(
        self,
        store: DuckDBStore,
        config: CompletenessConfig | None = None,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._store = store
        self._config = config or CompletenessConfig()
        self._clock = clock

    def scan(self, start: date, end: date) -> SessionScan:
        """Observe market sessions in [start, end] from current stored bars."""
        rows = self._store.conn.execute(
            """
            WITH span AS (
                SELECT instrument_id,
                       MIN(trade_date) AS first_d,
                       MAX(trade_date) AS last_d
                FROM daily_prices
                WHERE known_to IS NULL
                GROUP BY instrument_id
            ),
            present AS (
                SELECT trade_date AS d, COUNT(DISTINCT instrument_id) AS n
                FROM daily_prices
                WHERE known_to IS NULL AND trade_date BETWEEN ? AND ?
                GROUP BY trade_date
            )
            SELECT p.d,
                   p.n,
                   (SELECT COUNT(*) FROM span s WHERE s.first_d <= p.d AND s.last_d >= p.d)
            FROM present p
            ORDER BY p.d
            """,
            [start, end],
        ).fetchall()

        sessions: list[date] = []
        suspect: list[tuple[date, int, int]] = []
        for d, present, active in rows:
            if active < self._config.min_active_instruments:
                continue
            if present / active >= self._config.min_breadth:
                sessions.append(d)
            else:
                suspect.append((d, int(present), int(active)))
        return SessionScan(sessions=tuple(sessions), suspect_dates=tuple(suspect))

    def check(
        self,
        instrument_id: str,
        start: date,
        end: date,
        *,
        scan: SessionScan | None = None,
    ) -> CompletenessReport:
        """Report market sessions in [start, end] for which the instrument has no bar."""
        row = self._store.conn.execute(
            "SELECT MIN(trade_date) FROM daily_prices WHERE instrument_id = ? AND known_to IS NULL",
            [instrument_id],
        ).fetchone()
        first_bar = row[0] if row else None
        if first_bar is None:
            return CompletenessReport(
                instrument_id, start, end, CompletenessStatus.UNVERIFIED, detail="no stored bars"
            )

        scan = scan or self.scan(start, end)
        if not scan.verified:
            return CompletenessReport(
                instrument_id,
                start,
                end,
                CompletenessStatus.UNVERIFIED,
                detail=(
                    f"fewer than {self._config.min_active_instruments} trading instruments to "
                    "infer market sessions from"
                ),
            )

        lo = max(start, first_bar)
        expected = tuple(s for s in scan.sessions if lo <= s <= end)
        have = {
            r[0]
            for r in self._store.conn.execute(
                """
                SELECT DISTINCT trade_date FROM daily_prices
                WHERE instrument_id = ? AND known_to IS NULL AND trade_date BETWEEN ? AND ?
                """,
                [instrument_id, lo, end],
            ).fetchall()
        }
        settled = {
            r[0]
            for r in self._store.conn.execute(
                "SELECT DISTINCT trade_date FROM bhavcopy_files"
                " WHERE status = 'OK' AND trade_date BETWEEN ? AND ?",
                [lo, end],
            ).fetchall()
        }
        # A settled bhavcopy day without a bar: the stock did not trade on NSE (C6).
        missing = [s for s in expected if s not in have and s not in settled]
        suspect = tuple(d for d, _, _ in scan.suspect_dates if lo <= d <= end)
        if not missing:
            return CompletenessReport(
                instrument_id,
                start,
                end,
                CompletenessStatus.COMPLETE,
                expected_sessions=len(expected),
                suspect_dates=suspect,
            )
        return CompletenessReport(
            instrument_id,
            start,
            end,
            CompletenessStatus.INCOMPLETE,
            expected_sessions=len(expected),
            missing_sessions=tuple(missing),
            missing_ranges=_merge_ranges(expected, missing),
            suspect_dates=suspect,
        )
