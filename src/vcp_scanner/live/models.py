"""Plain data types of the live feed. A value the provider did not give is ``None``, never 0."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LiveQuote:
    key: str  # the provider's own key (NSE_EQ|ISIN, NSE:SYMBOL, NSE_INDEX|Nifty 50, ...)
    last_price: float
    prev_close: float | None
    change: float | None
    change_pct: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: int | None
    exchange_time: datetime | None  # the provider's last-trade time, when it gives one
    fetched_at: datetime  # when this process received it (IST)


@dataclass(frozen=True, slots=True)
class IntradayPoint:
    time: datetime
    value: float


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    symbol: str
    isin: str | None


@dataclass(frozen=True, slots=True)
class IndexSpec:
    id: str  # NIFTY50, SENSEX, NIFTY500
    label: str
    upstox_key: str
    kite_key: str


class LiveFeed(Protocol):
    """One provider behind the live display. The methods raise ``ProviderAuthError`` (no or
    rejected token), ``ProviderRateLimited`` (429) or ``ProviderError``; a key the provider does
    not return is simply absent from the result (shown as "not available")."""

    name: str
    max_batch: int
    min_call_gap_seconds: float

    def stock_key(self, symbol: str, isin: str | None) -> str | None: ...

    def index_key(self, spec: IndexSpec) -> str: ...

    def fetch(self, keys: Sequence[str], now: datetime, *, index: bool) -> dict[str, LiveQuote]: ...

    def intraday(self, key: str) -> list[IntradayPoint]: ...
