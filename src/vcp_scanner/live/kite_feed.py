"""Kite as the switchable alternative live feed: quotes by ``EXCHANGE:SYMBOL``.

Not checked against Kite's documentation yet (the page could not be opened in this session):
``ohlc.close`` is read as the previous session's close and the change is computed from it, which
avoids depending on the meaning of ``net_change``. Index intraday history needs Kite's instrument
tokens, which this feed does not load, so the index line is built from polled values only.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.providers.kite import KiteProvider
from vcp_scanner.live.models import IndexSpec, IntradayPoint, LiveQuote
from vcp_scanner.live.upstox_feed import _num


def _time(x: Any) -> datetime | None:
    if not isinstance(x, datetime):
        return None
    return x.replace(tzinfo=IST) if x.tzinfo is None else x.astimezone(IST)


def to_quote(
    key: str, val: dict[str, Any], fetched_at: datetime, *, index: bool
) -> LiveQuote | None:
    last = _num(val.get("last_price"))
    if last is None or last <= 0:
        return None  # missing is not zero
    ohlc: dict[str, Any] = val.get("ohlc") or {}
    prev = _num(ohlc.get("close"))
    prev = prev if prev and prev > 0 else None
    change = last - prev if prev is not None else None
    volume = _num(val.get("volume", val.get("volume_traded")))
    return LiveQuote(
        key=key, last_price=last, prev_close=prev, change=change,
        change_pct=change / prev * 100 if change is not None and prev else None,
        open=_num(ohlc.get("open")), high=_num(ohlc.get("high")), low=_num(ohlc.get("low")),
        volume=None if index or volume is None else int(volume),
        exchange_time=_time(val.get("last_trade_time")) or _time(val.get("timestamp")),
        fetched_at=fetched_at,
    )  # fmt: skip


class KiteFeed:
    name = "KITE"
    min_call_gap_seconds = 1.1  # Kite allows one quote call per second

    def __init__(self, provider: KiteProvider, batch: int = 200) -> None:
        self._p = provider
        self.max_batch = batch

    def stock_key(self, symbol: str, isin: str | None) -> str | None:
        return f"NSE:{symbol}"

    def index_key(self, spec: IndexSpec) -> str:
        return spec.kite_key

    def fetch(self, keys: Sequence[str], now: datetime, *, index: bool) -> dict[str, LiveQuote]:
        out: dict[str, LiveQuote] = {}
        for key, val in self._p.get_live_quotes(list(keys)).items():
            quote = to_quote(key, val, now, index=index)
            if quote is not None:
                out[key] = quote
        return out

    def intraday(self, key: str) -> list[IntradayPoint]:
        return []
