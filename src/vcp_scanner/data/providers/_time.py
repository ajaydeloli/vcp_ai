"""Shared time conventions for provider adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# The NSE trading calendar is defined in IST.
IST = ZoneInfo("Asia/Kolkata")


def daily_bar_timestamp(raw: datetime) -> datetime:
    """Normalise a provider's daily-bar stamp to UTC midnight of the IST trade date.

    Kite and Upstox stamp daily bars at IST midnight (+05:30). Converting that instant to
    UTC lands on 18:30 of the previous calendar day, which shifts every bar back one day.
    The trade date is therefore taken from the IST calendar day. Naive stamps are treated
    as IST.
    """
    if raw.tzinfo is None:
        raw = raw.replace(tzinfo=IST)
    trade_day = raw.astimezone(IST).date()
    return datetime(trade_day.year, trade_day.month, trade_day.day, tzinfo=UTC)
