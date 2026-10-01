"""Trading absences start a new price history (audit P1-2, owner decision 2026-10-01).

Every feature window counts rows, so a stock that was off NSE for months gets an SMA200, a
52-week range and an RS return that mix prices from before and after the absence. Such a
stock kept trading elsewhere or was suspended, so the jump on its return is not evidence of a
missed corporate action either: an action during the absence never reaches NSE's feed.

A ``TRADING_ABSENCE`` event marks the return bar of every absence of at least
``min_missed_sessions`` NSE sessions. It blocks signals, and like every dated block it ends
once the stock has ``data.quality.block_lifetime_bars`` bars from the return (P1-2b): from
then on no lookback window reaches back across the absence. Market sessions come from the
settled NSE bhavcopy days, so a holiday is never counted as a missed session.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date, datetime

from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity, make_event_id
from vcp_scanner.domain.market import Candle

FLAG = DataQualityFlag.TRADING_ABSENCE


def missed_sessions(sessions: Sequence[date], before: date, after: date) -> int:
    """Market sessions strictly between two bar dates (``sessions`` sorted ascending)."""
    return max(0, bisect_left(sessions, after) - bisect_right(sessions, before))


def trading_absence_events(
    instrument_id: str,
    candles: Sequence[Candle],
    sessions: Sequence[date],
    *,
    min_missed_sessions: int,
    detected_at: datetime,
) -> list[DataQualityEvent]:
    """One blocking event per return after ``min_missed_sessions`` or more missed sessions."""
    events: list[DataQualityEvent] = []
    ordered = sorted(candles, key=lambda c: c.timestamp)
    for prev, curr in zip(ordered, ordered[1:], strict=False):
        a, b = prev.timestamp.date(), curr.timestamp.date()
        missed = missed_sessions(sessions, a, b)
        if missed < min_missed_sessions:
            continue
        gap = (curr.open / prev.close - 1.0) if prev.close > 0 and curr.open > 0 else None
        events.append(
            DataQualityEvent(
                event_id=make_event_id(FLAG, instrument_id, b.isoformat()),
                instrument_id=instrument_id,
                flag=FLAG,
                severity=EventSeverity.HIGH,
                detected_at=detected_at,
                description=(
                    f"Back on NSE after missing {missed} sessions ({a} -> {b}); price history "
                    "restarts here, so features need a full lookback of new bars."
                ),
                context={
                    "prev_date": a.isoformat(),
                    "return_date": b.isoformat(),
                    "missed_sessions": missed,
                    "prev_close": prev.close,
                    "return_open": curr.open,
                    "gap_pct": None if gap is None else round(gap * 100, 2),
                },
                trade_date=b,
                blocks_signal=True,
                dataset="daily_prices",
            )
        )
    return events


def absence_spans(events: Sequence[DataQualityEvent]) -> dict[tuple[date, date], int]:
    """``(prev_date, return_date) -> missed sessions`` for the gap detector's bars."""
    spans: dict[tuple[date, date], int] = {}
    for e in events:
        ctx = e.context or {}
        prev, ret, missed = ctx.get("prev_date"), ctx.get("return_date"), ctx.get("missed_sessions")
        if isinstance(prev, str) and isinstance(ret, str) and isinstance(missed, int):
            spans[(date.fromisoformat(prev), date.fromisoformat(ret))] = missed
    return spans
