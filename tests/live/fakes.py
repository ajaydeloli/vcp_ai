"""A fake live feed and service builders: no network, no credentials, labelled synthetic."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta

from vcp_scanner.data.providers._time import IST
from vcp_scanner.live.config import LiveConfig
from vcp_scanner.live.hours import MarketCalendar
from vcp_scanner.live.models import (
    IndexSpec,
    IntradayPoint,
    LiveFeed,
    LiveQuote,
    UniverseEntry,
)
from vcp_scanner.live.service import LiveService

#: Tuesday, market open (09:15 to 15:30 IST).
OPEN_NOW = datetime(2026, 10, 6, 11, 0, tzinfo=IST)
SETTLED = datetime(2026, 10, 6, 15, 45, tzinfo=IST)
SATURDAY = datetime(2026, 10, 10, 12, 0, tzinfo=IST)


class Clock:
    def __init__(self, now: datetime = OPEN_NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def quote(key: str, price: float, now: datetime, *, index: bool = False) -> LiveQuote:
    prev = round(price / 1.02, 2)
    return LiveQuote(
        key=key, last_price=price, prev_close=prev, change=round(price - prev, 2),
        change_pct=round((price - prev) / prev * 100, 2), open=prev, high=price + 1,
        low=prev - 1, volume=None if index else 123456, exchange_time=now, fetched_at=now,
    )  # fmt: skip


class FakeFeed:
    """Prices by provider key. ``script`` holds one entry per fetch call: an exception to raise,
    or None to answer normally."""

    name = "FAKE"
    max_batch = 2
    min_call_gap_seconds = 0.0

    def __init__(self, prices: dict[str, float]) -> None:
        self.prices = dict(prices)
        self.script: list[Exception | None] = []
        self.calls: list[tuple[list[str], bool]] = []
        self.intraday_points: dict[str, list[IntradayPoint]] = {}
        self.intraday_calls: list[str] = []

    def stock_key(self, symbol: str, isin: str | None) -> str | None:
        return None if isin is None else f"FAKE|{symbol}"

    def index_key(self, spec: IndexSpec) -> str:
        return f"IDX|{spec.id}"

    def fetch(self, keys: Sequence[str], now: datetime, *, index: bool) -> dict[str, LiveQuote]:
        self.calls.append((list(keys), index))
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step
        return {k: quote(k, self.prices[k], now, index=index) for k in keys if k in self.prices}

    def intraday(self, key: str) -> list[IntradayPoint]:
        self.intraday_calls.append(key)
        return list(self.intraday_points.get(key, []))


UNIVERSE = [
    UniverseEntry("ALPHA", "INE000A01010"),
    UniverseEntry("BETA", "INE000B01010"),
    UniverseEntry("GAMMA", None),  # no ISIN: Upstox has no key for it
    UniverseEntry("DELTA", "INE000D01010"),  # in the universe, but the feed has no price
    UniverseEntry("EPSILON", "INE000E01010"),
]
PRICES = {
    "FAKE|ALPHA": 102.0, "FAKE|BETA": 51.0, "FAKE|EPSILON": 20.4,
    "IDX|NIFTY50": 25500.0, "IDX|SENSEX": 83000.0,
}  # fmt: skip


def make_service(
    clock: Clock | Callable[[], datetime],
    feed: FakeFeed | None = None,
    *,
    universe: list[UniverseEntry] | None = None,
    holidays: set[date] | None = None,
    config: LiveConfig | None = None,
    factory: Callable[[], LiveFeed] | None = None,
    autostart: bool = False,
) -> tuple[LiveService, FakeFeed]:
    feed = feed or FakeFeed(PRICES)
    cfg = config or LiveConfig(poll_interval_seconds=15.0)
    loader = (lambda: set(holidays)) if holidays is not None else None
    cal = MarketCalendar(loader, background=False)
    svc = LiveService(
        cfg, factory or (lambda: feed), lambda: list(universe or UNIVERSE), cal, clock,
        autostart=autostart, sleep=lambda _s: None,
    )  # fmt: skip
    return svc, feed
