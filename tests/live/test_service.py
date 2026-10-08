"""The live service with a fake feed: caching, hours, failures. No network, no credentials."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from tests.live.fakes import (
    OPEN_NOW,
    SATURDAY,
    SETTLED,
    Clock,
    FakeFeed,
    make_service,
)
from vcp_scanner.data.providers._time import IST
from vcp_scanner.domain.errors import ProviderAuthError, ProviderError, ProviderRateLimited
from vcp_scanner.live.config import LiveConfig
from vcp_scanner.live.models import IntradayPoint, LiveFeed


def test_a_poll_fetches_indices_then_stocks_in_batches() -> None:
    svc, feed = make_service(Clock())
    assert svc.poll_once() is True
    index_calls = [c for c in feed.calls if c[1]]
    stock_calls = [c for c in feed.calls if not c[1]]
    assert len(index_calls) == 1 and len(index_calls[0][0]) == 3  # one call for the 3 indices
    # 4 stocks have a key (GAMMA has no ISIN); batches of 2
    assert [len(c[0]) for c in stock_calls] == [2, 2]
    assert svc.view("alpha").quote is not None  # symbols are case-insensitive
    assert svc.view("ALPHA").mode == "live"


def test_missing_is_not_zero() -> None:
    svc, _ = make_service(Clock())
    svc.poll_once()
    assert svc.view("GAMMA").quote is None and "no key" in (svc.view("GAMMA").reason or "")
    assert svc.view("DELTA").quote is None and "no price" in (svc.view("DELTA").reason or "")
    assert svc.view("ZZZ").reason == "not in the scanned universe"
    for v in svc.index_views():
        if v.spec.id == "NIFTY500":
            assert v.view.quote is None and v.points == []  # the feed gave no NIFTY 500


def test_delay_counts_from_the_receipt_of_the_price() -> None:
    clock = Clock()
    svc, _ = make_service(clock)
    svc.poll_once()
    clock.advance(12)
    v = svc.view("ALPHA")
    assert v.mode == "live" and v.delay_seconds == 12
    clock.advance(200)  # no poll since: the price is stale, still shown with its age
    v = svc.view("ALPHA")
    assert v.mode == "stale" and v.delay_seconds == 212
    assert svc.status().state == "stale"


def test_coverage_is_reported() -> None:
    svc, _ = make_service(Clock())
    svc.poll_once()
    s = svc.status()
    assert (s.stocks_covered, s.stocks_total) == (3, 5)
    assert s.state == "live" and s.message == "2 of 5 stocks have no price"


def test_closed_market_takes_one_closing_snapshot_and_stops() -> None:
    clock = Clock(SATURDAY)
    svc, feed = make_service(clock)
    assert svc.poll_once() is True  # no closing snapshot yet: one fetch
    n = len(feed.calls)
    assert svc.poll_once() is False and len(feed.calls) == n  # nothing more until the next open
    v = svc.view("ALPHA")
    assert v.mode == "closed" and v.delay_seconds is None
    assert v.session_date is not None and v.session_date.isoformat() == "2026-10-09"
    assert svc.status().state == "closed" and svc.status().market == "closed"


def test_pre_open_makes_no_fetch() -> None:
    svc, feed = make_service(Clock(datetime(2026, 10, 6, 9, 5, tzinfo=IST)))
    assert svc.poll_once() is False and feed.calls == []
    assert svc.status().market == "pre_open"


def test_polling_continues_until_the_close_is_settled() -> None:
    clock = Clock(datetime(2026, 10, 6, 15, 35, tzinfo=IST))  # closed, before 15:40
    svc, feed = make_service(clock)
    assert svc.poll_once() is True
    clock.advance(20)
    assert svc.poll_once() is True  # the closing price may still move: keep going
    clock.now = SETTLED
    assert svc.poll_once() is True  # the settled snapshot
    n = len(feed.calls)
    clock.advance(30)
    assert svc.poll_once() is False and len(feed.calls) == n


def test_a_missing_token_says_so_and_retries_with_a_rebuilt_feed() -> None:
    clock = Clock()
    built: list[int] = []
    good = FakeFeed({"FAKE|ALPHA": 100.0})

    def factory() -> LiveFeed:
        built.append(1)
        if len(built) == 1:
            raise ProviderAuthError("UPSTOX_ACCESS_TOKEN is not set")
        return good

    svc, _ = make_service(clock, factory=factory)
    svc.poll_once()
    s = svc.status()
    assert s.state == "token_needed" and "fresh token" in (s.message or "")
    assert s.retry_at == OPEN_NOW + timedelta(seconds=60)
    assert svc.view("ALPHA").quote is None
    clock.advance(30)
    assert svc.poll_once() is False and len(built) == 1  # backing off
    clock.advance(31)
    assert svc.poll_once() is True and len(built) == 2  # .env re-read, feed rebuilt
    assert svc.status().state == "live" and svc.view("ALPHA").quote is not None


def test_a_rejected_token_keeps_the_last_prices_with_their_age() -> None:
    clock = Clock()
    svc, feed = make_service(clock)
    svc.poll_once()
    clock.advance(130)
    feed.script = [ProviderAuthError("Upstox quotes: HTTP 401")]
    svc.poll_once()
    assert svc.status().state == "token_needed"
    v = svc.view("ALPHA")
    assert v.quote is not None and v.mode == "stale" and v.delay_seconds == 130


def test_rate_limit_is_reported_and_waited_out() -> None:
    clock = Clock()
    svc, feed = make_service(clock)
    svc.poll_once()
    clock.advance(15)
    feed.script = [ProviderRateLimited("Upstox quotes: HTTP 429", retry_after=90)]
    svc.poll_once()
    s = svc.status()
    assert s.state == "rate_limited" and "rate limit" in (s.message or "")
    assert s.retry_at is not None and s.retry_at >= clock.now + timedelta(seconds=90)
    n = len(feed.calls)
    clock.advance(60)
    assert svc.poll_once() is False and len(feed.calls) == n  # still waiting
    clock.advance(31)
    assert svc.poll_once() is True and svc.status().state == "live"


def test_errors_back_off_and_recover() -> None:
    clock = Clock()
    svc, feed = make_service(clock, config=LiveConfig(poll_interval_seconds=15.0))
    down = [None, ProviderError("HTTP 503"), ProviderError("HTTP 503")]  # indices ok, stocks fail
    feed.script = list(down)
    svc.poll_once()
    first = svc.status()
    assert first.state == "error" and "HTTP 503" in (first.message or "")
    assert first.retry_at == clock.now + timedelta(seconds=15)
    clock.advance(16)
    feed.script = list(down)
    svc.poll_once()
    assert svc.status().retry_at == clock.now + timedelta(seconds=30)  # doubled
    clock.advance(31)
    svc.poll_once()
    assert svc.status().state == "live" and svc.status().message == "2 of 5 stocks have no price"


def test_one_failed_stock_batch_does_not_hide_the_others() -> None:
    svc, feed = make_service(Clock())
    feed.script = [None, ProviderError("HTTP 500"), None]  # indices ok, stock batch 1 fails
    svc.poll_once()
    assert svc.status().state == "live"
    assert svc.view("ALPHA").quote is None  # in the failed batch
    assert svc.view("EPSILON").quote is not None  # the other batch arrived


def test_every_stock_batch_failing_is_an_error() -> None:
    svc, feed = make_service(Clock())
    feed.script = [None] + [ProviderError("HTTP 500")] * 2
    svc.poll_once()
    assert svc.status().state == "error"


def test_a_rejected_index_key_does_not_hide_the_other_indices() -> None:
    clock = Clock()
    svc, feed = make_service(clock)
    # the batch call fails, then one call per index: the SENSEX one fails too
    feed.script = [ProviderError("HTTP 400 invalid key"), None, ProviderError("HTTP 400"), None]
    svc.poll_once()
    views = {v.spec.id: v for v in svc.index_views()}
    assert views["NIFTY50"].view.quote is not None
    assert views["SENSEX"].view.quote is None
    n_index_calls = sum(1 for c in feed.calls if c[1])
    clock.advance(16)
    svc.poll_once()
    sent = [c[0] for c in feed.calls if c[1]][n_index_calls:]
    assert all("IDX|SENSEX" not in keys for keys in sent)  # skipped for an hour


def test_the_index_line_is_backfilled_once_and_grows() -> None:
    clock = Clock()
    svc, feed = make_service(clock)
    t0 = OPEN_NOW - timedelta(minutes=2)
    feed.intraday_points["IDX|NIFTY50"] = [
        IntradayPoint(t0, 25400.0), IntradayPoint(t0 + timedelta(minutes=1), 25450.0),
    ]  # fmt: skip
    svc.poll_once()
    clock.advance(60)
    svc.poll_once()
    nifty = next(v for v in svc.index_views() if v.spec.id == "NIFTY50")
    values = [p.value for p in nifty.points]
    assert values == [25400.0, 25450.0, 25500.0, 25500.0]
    assert feed.intraday_calls.count("IDX|NIFTY50") == 1  # one backfill per index and session


def test_the_index_line_starts_again_each_session() -> None:
    clock = Clock()
    svc, _ = make_service(clock)
    svc.poll_once()
    clock.now = datetime(2026, 10, 7, 11, 0, tzinfo=IST)  # next day
    svc.poll_once()
    nifty = next(v for v in svc.index_views() if v.spec.id == "NIFTY50")
    assert len(nifty.points) == 1 and nifty.points[0].time.date().isoformat() == "2026-10-07"


def test_yesterdays_price_is_a_close_not_live() -> None:
    clock = Clock(datetime(2026, 10, 5, 14, 0, tzinfo=IST))  # Monday session
    svc, feed = make_service(clock)
    svc.poll_once()
    clock.now = datetime(2026, 10, 6, 10, 0, tzinfo=IST)  # Tuesday, open; the feed is down
    feed.script = [ProviderError("HTTP 503")] * 10  # every call fails
    svc.poll_once()
    assert svc.status().state == "error"
    v = svc.view("ALPHA")
    assert v.mode == "closed" and v.session_date is not None
    assert v.session_date.isoformat() == "2026-10-05" and v.delay_seconds is None


def test_disabled_does_nothing() -> None:
    svc, feed = make_service(Clock(), config=LiveConfig(enabled=False))
    assert svc.poll_once() is False and feed.calls == []
    s = svc.status()
    assert s.state == "disabled" and "switched off" in (s.message or "")
    assert svc.view("ALPHA").quote is None


def test_touch_without_autostart_never_polls() -> None:
    svc, feed = make_service(Clock())
    svc.touch()
    assert feed.calls == []
    assert svc.status().state == "starting"


def test_the_poller_thread_starts_on_demand_and_stops() -> None:
    cfg = LiveConfig(poll_interval_seconds=0.01, idle_after_seconds=3600.0)
    svc, feed = make_service(Clock(), config=cfg, autostart=True)
    svc.touch()  # first answer is not empty: one synchronous cycle, then the thread
    assert svc.view("ALPHA").quote is not None
    deadline = time.monotonic() + 3
    while len(feed.calls) < 12 and time.monotonic() < deadline:
        time.sleep(0.01)
    svc.stop()
    assert len(feed.calls) >= 12  # the thread kept polling
