"""NSE cash-market hours: 09:15 to 15:30 IST, Monday to Friday, not on exchange holidays.

Holidays come from a loader (NSE's list, current calendar year), fetched in a background thread by
``refresh`` so a request never waits for NSE. Until the list is in, or if it cannot be loaded, the
weekday rule alone applies and ``holidays_known`` is False. Special sessions (e.g. Muhurat trading)
are not covered and show as closed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

from vcp_scanner.data.providers._time import IST

logger = logging.getLogger(__name__)

PRE_OPEN_START = time(9, 0)  # no fetches from here to the open: feeds may show indicative prices
OPEN_TIME = time(9, 15)
CLOSE_TIME = time(15, 30)
SETTLE_TIME = time(15, 40)  # the closing price is final from here
HOLIDAY_REFRESH = timedelta(hours=24)
HOLIDAY_RETRY = timedelta(hours=1)


class MarketCalendar:
    def __init__(
        self, load_holidays: Callable[[], set[date]] | None = None, *, background: bool = True
    ) -> None:
        self._load = load_holidays
        self._background = background
        self._holidays: set[date] = set()
        self.holidays_known = False
        self._next_load: datetime | None = None
        self._lock = threading.Lock()

    def refresh(self, now: datetime) -> None:
        """Load the holiday list when it is due (never blocks the caller unless
        ``background`` is False, which tests use)."""
        if self._load is None:
            return
        with self._lock:
            if self._next_load is not None and now < self._next_load:
                return
            self._next_load = now + HOLIDAY_RETRY  # provisional: a failure retries in an hour
        if self._background:
            threading.Thread(target=self._load_now, args=(now,), daemon=True).start()
        else:
            self._load_now(now)

    def _load_now(self, now: datetime) -> None:
        assert self._load is not None
        try:
            holidays = set(self._load())
        except Exception as exc:  # network or payload trouble: the weekday rule keeps applying
            logger.warning("NSE holiday list unavailable (%s)", type(exc).__name__)
            return
        with self._lock:
            self._holidays, self.holidays_known = holidays, True
            self._next_load = now + HOLIDAY_REFRESH

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self._holidays

    def is_open(self, now: datetime) -> bool:
        now = now.astimezone(IST)
        return self.is_trading_day(now.date()) and OPEN_TIME <= now.time() < CLOSE_TIME

    def is_pre_open(self, now: datetime) -> bool:
        now = now.astimezone(IST)
        return self.is_trading_day(now.date()) and PRE_OPEN_START <= now.time() < OPEN_TIME

    def last_closed_session(self, now: datetime) -> date | None:
        """The most recent session that has finished trading (15:30) at ``now``."""
        now = now.astimezone(IST)
        day = now.date()
        for back in range(11):
            d = day - timedelta(days=back)
            if self.is_trading_day(d) and (d < day or now.time() >= CLOSE_TIME):
                return d
        return None

    @staticmethod
    def settled_at(session: date) -> datetime:
        return datetime.combine(session, SETTLE_TIME, tzinfo=IST)
