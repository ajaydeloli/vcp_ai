"""The NSE session calendar as known at a cutoff (audit P2-2).

Sessions are the days whose NSE bhavcopy settled as OK. When no settled bhavcopy is known
(a Kite-only database, or a cutoff before the bhavcopy backfill) the calendar falls back to
the distinct dates of the raw bars known then, so staleness still has sessions to count.
"""

from __future__ import annotations

from datetime import date, datetime

import duckdb


def load_session_calendar(
    conn: duckdb.DuckDBPyConnection, as_of_date: date, known_at: datetime | None = None
) -> list[date]:
    """Sessions on or before ``as_of_date``, sorted; with ``known_at``, only those recorded
    (bhavcopy) or known (raw bars) by then, so a past run sees the calendar it saw."""
    rows = conn.execute(
        """
        SELECT DISTINCT trade_date FROM bhavcopy_files
        WHERE status = 'OK' AND trade_date <= ?
          AND (CAST(? AS TIMESTAMPTZ) IS NULL OR recorded_at <= ?)
        ORDER BY trade_date
        """,
        [as_of_date, known_at, known_at],
    ).fetchall()
    if rows:
        return [r[0] for r in rows]
    rows = conn.execute(
        """
        SELECT DISTINCT trade_date FROM daily_prices
        WHERE trade_date <= ?
          AND (CAST(? AS TIMESTAMPTZ) IS NULL
               OR (known_from <= ? AND (known_to IS NULL OR known_to > ?)))
          AND (CAST(? AS TIMESTAMPTZ) IS NOT NULL OR known_to IS NULL)
        ORDER BY trade_date
        """,
        [as_of_date, known_at, known_at, known_at, known_at],
    ).fetchall()
    return [r[0] for r in rows]
