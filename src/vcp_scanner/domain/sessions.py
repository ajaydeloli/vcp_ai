"""Market-session arithmetic (audit P2-2). Pure functions over a sorted session calendar."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from datetime import date


def missed_sessions_since(sessions: Sequence[date], last_bar: date, as_of: date) -> int:
    """Sessions after ``last_bar`` up to and including ``as_of`` (``sessions`` sorted).

    0 when the stock traded on the latest session; a holiday or weekend is never counted
    because it is not a session.
    """
    return max(0, bisect_right(sessions, as_of) - bisect_right(sessions, last_bar))
