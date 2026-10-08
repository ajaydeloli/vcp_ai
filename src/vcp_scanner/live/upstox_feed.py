"""Upstox as the live feed: quotes by ISIN key, indices by their own key."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.providers.upstox import UpstoxProvider, upstox_instrument_key
from vcp_scanner.domain.market import Instrument
from vcp_scanner.live.models import IndexSpec, IntradayPoint, LiveQuote


def _num(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _ms_to_time(x: Any) -> datetime | None:
    value = _num(x)
    if value is None or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000.0, IST)


def to_quote(
    key: str, val: dict[str, Any], fetched_at: datetime, *, index: bool
) -> LiveQuote | None:
    """One Upstox quote entry as a LiveQuote, or None when it has no usable last price.

    ``net_change`` is documented as the change from yesterday's close, so the previous close is
    derived from it; the documentation does not say what ``ohlc.close`` holds, so it is unused.
    """
    last = _num(val.get("last_price"))
    if last is None or last <= 0:
        return None  # missing is not zero: never invent a price
    change = _num(val.get("net_change"))
    prev = last - change if change is not None else None
    pct = change / prev * 100 if change is not None and prev else None
    ohlc: dict[str, Any] = val.get("ohlc") or {}
    volume = _num(val.get("volume"))
    return LiveQuote(
        key=key, last_price=last, prev_close=prev, change=change, change_pct=pct,
        open=_num(ohlc.get("open")), high=_num(ohlc.get("high")), low=_num(ohlc.get("low")),
        volume=None if index or volume is None else int(volume),  # indices carry no volume
        exchange_time=_ms_to_time(val.get("last_trade_time")), fetched_at=fetched_at,
    )  # fmt: skip


class UpstoxFeed:
    name = "UPSTOX"
    max_batch = 500  # Upstox: 500 instrument keys per quotes call
    min_call_gap_seconds = 0.1  # limits: 25/s, 250/min, 1000 per 30 min per API

    def __init__(self, provider: UpstoxProvider) -> None:
        self._p = provider

    def stock_key(self, symbol: str, isin: str | None) -> str | None:
        # Reuses the provider's key rule (NSE_EQ|<ISIN>); no ISIN means no key.
        return upstox_instrument_key(Instrument(instrument_id=symbol, symbol=symbol, isin=isin))

    def index_key(self, spec: IndexSpec) -> str:
        return spec.upstox_key

    def fetch(self, keys: Sequence[str], now: datetime, *, index: bool) -> dict[str, LiveQuote]:
        out: dict[str, LiveQuote] = {}
        for key, val in self._p.get_full_quotes(keys).items():
            quote = to_quote(key, val, now, index=index)
            if quote is not None:
                out[key] = quote
        return out

    def intraday(self, key: str) -> list[IntradayPoint]:
        points: list[IntradayPoint] = []
        for row in self._p.get_intraday_candles(key, "1minute"):
            close = _num(row[4]) if len(row) > 4 else None
            if close is None or close <= 0:
                continue
            points.append(IntradayPoint(datetime.fromisoformat(str(row[0])).astimezone(IST), close))
        return points
