"""The Upstox and Kite feeds: payload mapping, errors that never carry a token, config.

Payloads are synthetic samples shaped like the providers' documented responses (labelled
synthetic: AGENTS.md rule 10). No network is used.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import requests

from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.providers.kite import KiteProvider
from vcp_scanner.data.providers.upstox import UpstoxProvider
from vcp_scanner.domain.errors import (
    ConfigError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
)
from vcp_scanner.live import kite_feed, upstox_feed
from vcp_scanner.live.config import load_live_config
from vcp_scanner.live.models import IndexSpec

NOW = datetime(2026, 10, 6, 11, 0, tzinfo=IST)
SECRET = "tok-SECRET-123"
REPO = Path(__file__).resolve().parents[2]

UPSTOX_STOCK: dict[str, Any] = {
    "last_price": 102.0, "net_change": 2.0, "volume": 5400,
    "ohlc": {"open": 100.0, "high": 103.5, "low": 99.5, "close": 100.0},
    "last_trade_time": 1791264000000, "instrument_token": "NSE_EQ|INE000A01010",
}  # fmt: skip


def test_upstox_quote_mapping() -> None:
    q = upstox_feed.to_quote("NSE_EQ|INE000A01010", UPSTOX_STOCK, NOW, index=False)
    assert q is not None
    assert (q.last_price, q.change, q.prev_close) == (102.0, 2.0, 100.0)
    assert q.change_pct == pytest.approx(2.0)
    assert (q.open, q.high, q.low, q.volume) == (100.0, 103.5, 99.5, 5400)
    assert q.exchange_time is not None and q.exchange_time.tzinfo is not None


def test_upstox_gaps_stay_gaps() -> None:
    bare = {"last_price": 50.0}
    q = upstox_feed.to_quote("k", bare, NOW, index=False)
    assert q is not None
    assert q.change is None and q.prev_close is None and q.change_pct is None
    assert q.open is None and q.high is None and q.low is None and q.volume is None
    assert q.exchange_time is None
    assert upstox_feed.to_quote("k", {"last_price": 0}, NOW, index=False) is None
    assert upstox_feed.to_quote("k", {"last_price": None}, NOW, index=False) is None
    assert upstox_feed.to_quote("k", {}, NOW, index=False) is None


def test_an_index_has_no_volume() -> None:
    q = upstox_feed.to_quote("i", {**UPSTOX_STOCK, "volume": 0}, NOW, index=True)
    assert q is not None and q.volume is None


class StubUpstox:
    def __init__(self, data: dict[str, dict[str, Any]]) -> None:
        self.data = data

    def get_full_quotes(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.data.items() if k in keys}

    def get_intraday_candles(self, key: str, interval: str) -> list[list[Any]]:
        return [["2026-10-06T09:15:00+05:30", 1, 2, 0.5, 1.5, 10, 0], ["x", 1, 2, 3, None]]


def test_upstox_feed_keys_and_fetch() -> None:
    feed = upstox_feed.UpstoxFeed(StubUpstox({"NSE_EQ|INE000A01010": UPSTOX_STOCK}))  # type: ignore[arg-type]
    assert feed.stock_key("ALPHA", "INE000A01010") == "NSE_EQ|INE000A01010"
    assert feed.stock_key("GAMMA", None) is None  # no ISIN, no key
    spec = IndexSpec("NIFTY50", "NIFTY 50", "NSE_INDEX|Nifty 50", "NSE:NIFTY 50")
    assert feed.index_key(spec) == "NSE_INDEX|Nifty 50"
    got = feed.fetch(["NSE_EQ|INE000A01010", "NSE_EQ|MISSING"], NOW, index=False)
    assert list(got) == ["NSE_EQ|INE000A01010"]  # a key the provider omits is just absent
    points = feed.intraday("NSE_INDEX|Nifty 50")
    assert [p.value for p in points] == [1.5]  # the row without a close is dropped


KITE_STOCK: dict[str, Any] = {
    "last_price": 102.0, "volume": 5400,
    "ohlc": {"open": 100, "high": 103, "low": 99, "close": 100},
    "last_trade_time": datetime(2026, 10, 6, 10, 59, 58),  # noqa: DTZ001 (Kite sends naive IST)
}  # fmt: skip


def test_kite_quote_mapping_computes_the_change_from_the_previous_close() -> None:
    q = kite_feed.to_quote("NSE:ALPHA", KITE_STOCK, NOW, index=False)
    assert q is not None
    assert (q.prev_close, q.change) == (100.0, 2.0) and q.change_pct == pytest.approx(2.0)
    assert q.exchange_time == datetime(2026, 10, 6, 10, 59, 58, tzinfo=IST)
    no_close = kite_feed.to_quote("k", {"last_price": 10.0, "ohlc": {"close": 0}}, NOW, index=False)
    assert no_close is not None and no_close.prev_close is None and no_close.change is None
    assert kite_feed.to_quote("k", {"last_price": 0}, NOW, index=False) is None


class Resp:
    def __init__(
        self, status: int, body: Any = None, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = f"echo {SECRET}"  # a provider that echoes things back

    def json(self) -> Any:
        return self._body


class Session:
    def __init__(self, resp: Resp | Exception) -> None:
        self.resp, self.calls = resp, 0

    def get(self, url: str, **_: Any) -> Resp:
        self.calls += 1
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def upstox_with(
    resp: Resp | Exception, token: str | None = SECRET
) -> tuple[UpstoxProvider, Session]:
    p = UpstoxProvider("key", token)
    s = Session(resp)
    p._live_session = s  # type: ignore[assignment]
    return p, s


def test_upstox_errors_are_typed_and_never_carry_the_token() -> None:
    cases: list[tuple[Resp | Exception, type[Exception]]] = [
        (Resp(401), ProviderAuthError),
        (Resp(403), ProviderAuthError),
        (Resp(429, headers={"Retry-After": "7"}), ProviderRateLimited),
        (Resp(500), ProviderError),
        (requests.ConnectionError(f"boom {SECRET}"), ProviderError),
    ]
    for resp, expected in cases:
        p, _ = upstox_with(resp)
        with pytest.raises(expected) as info:
            p.get_full_quotes(["NSE_EQ|X"])
        assert SECRET not in str(info.value)


def test_upstox_429_carries_the_wait_and_success_is_keyed_by_instrument_token() -> None:
    p, _ = upstox_with(Resp(429, headers={"Retry-After": "7"}))
    with pytest.raises(ProviderRateLimited) as info:
        p.get_full_quotes(["NSE_EQ|X"])
    assert info.value.retry_after == 7.0
    entry = {"last_price": 1.0, "instrument_token": "NSE_EQ|INE000A01010"}
    body = {"data": {"NSE_EQ:ALPHA": entry}}
    p, s = upstox_with(Resp(200, body))
    assert list(p.get_full_quotes(["NSE_EQ|INE000A01010"])) == ["NSE_EQ|INE000A01010"]
    assert s.calls == 1


def test_upstox_without_a_token_does_not_call_out() -> None:
    p, s = upstox_with(Resp(200, {}), token=None)
    with pytest.raises(ProviderAuthError):
        p.get_full_quotes(["NSE_EQ|X"])
    assert s.calls == 0


class FakeKite:
    def __init__(self, error: Exception | None = None, data: dict[str, Any] | None = None) -> None:
        self.error, self.data = error, data or {}

    def quote(self, keys: list[str]) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.data


def test_kite_errors_are_typed_and_never_carry_the_token() -> None:
    from kiteconnect.exceptions import NetworkException, TokenException

    for error, expected in (
        (TokenException(f"Incorrect api_key or access_token {SECRET}"), ProviderAuthError),
        (NetworkException(f"Too many requests {SECRET}"), ProviderRateLimited),
        (RuntimeError(f"boom {SECRET}"), ProviderError),
    ):
        p = KiteProvider("key", SECRET)
        p._kite = FakeKite(error)  # type: ignore[assignment]
        with pytest.raises(expected) as info:
            p.get_live_quotes(["NSE:ALPHA"])
        assert SECRET not in str(info.value)
    p = KiteProvider("key", None)
    with pytest.raises(ProviderAuthError):
        p.get_live_quotes(["NSE:ALPHA"])


def test_kite_feed_fetch_and_keys() -> None:
    p = KiteProvider("key", SECRET)
    p._kite = FakeKite(data={"NSE:ALPHA": KITE_STOCK})  # type: ignore[assignment]
    feed = kite_feed.KiteFeed(p, batch=3)
    assert feed.max_batch == 3 and feed.stock_key("ALPHA", None) == "NSE:ALPHA"
    got = feed.fetch(["NSE:ALPHA", "NSE:NOPE"], NOW, index=False)
    assert list(got) == ["NSE:ALPHA"]
    assert feed.intraday("NSE:NIFTY 50") == []


def test_the_shipped_config_loads_and_picks_upstox_first() -> None:
    cfg = load_live_config(REPO / "config")
    assert cfg.enabled and cfg.provider == "upstox"
    assert [i.id for i in cfg.indices] == ["NIFTY50", "SENSEX", "NIFTY500"]


def test_config_defaults_and_errors(tmp_path: Path) -> None:
    assert load_live_config(tmp_path).provider == "upstox"  # no file: defaults
    (tmp_path / "live.yaml").write_text("provider: dhan\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="provider"):
        load_live_config(tmp_path)
    (tmp_path / "live.yaml").write_text("pollinterval: 3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown keys"):
        load_live_config(tmp_path)
    (tmp_path / "live.yaml").write_text("poll_interval_seconds: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="at least 5"):
        load_live_config(tmp_path)
    (tmp_path / "live.yaml").write_text("provider: kite\nkite_batch: 50\n", encoding="utf-8")
    cfg = load_live_config(tmp_path)
    assert cfg.provider == "kite" and cfg.kite_batch == 50
