"""The live-price service: an in-memory cache fed by one poller, read by the API.

Display only. It never writes anywhere (no database, no file) and nothing reads it but the API.

* Polling is demand driven: it runs while a page has asked for live data in the last
  ``idle_after_seconds``, and only when a fetch is useful (market open, or one closing snapshot
  after 15:40 IST, never between 09:00 and 09:15 when feeds may show indicative prices).
* A failure never switches provider. It is reported (``token_needed``, ``rate_limited``, ``error``)
  and the last quotes stay visible with their age.
* Endpoints read the cache only, so page loads cannot add provider calls.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from vcp_scanner.data.providers._time import IST
from vcp_scanner.domain.errors import ProviderAuthError, ProviderError, ProviderRateLimited
from vcp_scanner.live.config import LiveConfig
from vcp_scanner.live.hours import MarketCalendar
from vcp_scanner.live.models import (
    IndexSpec,
    IntradayPoint,
    LiveFeed,
    LiveQuote,
    UniverseEntry,
)

logger = logging.getLogger(__name__)

FAILURE_STATES = ("token_needed", "rate_limited", "error")
MAX_POINTS = 500
BAD_KEY_RETRY = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class FeedStatus:
    state: str  # disabled | starting | live | stale | closed | token_needed | rate_limited | error
    message: str | None
    provider: str
    market: str  # open | pre_open | closed
    last_success_at: datetime | None
    retry_at: datetime | None
    stocks_covered: int | None
    stocks_total: int | None
    holidays_known: bool


@dataclass(frozen=True, slots=True)
class QuoteView:
    quote: LiveQuote | None
    mode: str | None  # live | stale | closed
    session_date: date | None
    delay_seconds: int | None  # seconds since this process received the price (live/stale only)
    reason: str | None  # why there is no price


@dataclass(frozen=True, slots=True)
class IndexView:
    spec: IndexSpec
    view: QuoteView
    points: list[IntradayPoint]


class LiveService:
    def __init__(
        self,
        config: LiveConfig,
        feed_factory: Callable[[], LiveFeed],
        universe: Callable[[], list[UniverseEntry]],
        calendar: MarketCalendar,
        clock: Callable[[], datetime],
        *,
        autostart: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cfg = config
        self._factory = feed_factory
        self._universe = universe
        self._cal = calendar
        self._clock = clock
        self._autostart = autostart
        self._sleep = sleep
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._feed: LiveFeed | None = None
        self._quotes: dict[str, LiveQuote] = {}
        self._symbol_keys: dict[str, str | None] = {}
        self._keys_built = False
        self._points: dict[str, dict[datetime, float]] = {}
        self._points_session: dict[str, date | None] = {}
        self._backfilled: set[tuple[str, date | None]] = set()
        self._bad_keys: dict[str, datetime] = {}
        self._index_keys: dict[str, str] = {}
        self._state = "starting"
        self._message: str | None = None
        self._last_success: datetime | None = None
        self._last_attempt: datetime | None = None
        self._last_demand: datetime = clock()
        self._retry_at: datetime | None = None
        self._failures = 0
        self._covered: int | None = None
        self._total: int | None = None
        self._snapshot_session: date | None = None

    # ---- demand and the poller thread ------------------------------------------------------

    def touch(self) -> None:
        """A page asked for live data: keep (or start) polling."""
        if not self._cfg.enabled:
            return
        now = self._clock()
        with self._lock:
            self._last_demand = now
            running = self._thread is not None and self._thread.is_alive()
        if not self._autostart:
            return
        if not running:
            self.poll_once()  # the first answer after a start or an idle stop is not empty
            self._ensure_thread()

    def stop(self) -> None:
        self._stop.set()

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="live-feed", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        me = threading.current_thread()
        while not self._stop.is_set():
            with self._lock:
                idle = (self._clock() - self._last_demand).total_seconds()
            if idle > self._cfg.idle_after_seconds:
                break
            try:
                self.poll_once()
            except Exception as exc:  # display only: a crash must not take the API down
                logger.warning("live poll failed: %s", type(exc).__name__)
            if self._stop.wait(self._cfg.poll_interval_seconds):
                break
        with self._lock:
            if self._thread is me:
                self._thread = None

    # ---- one polling cycle -----------------------------------------------------------------

    def _should_fetch(self, now: datetime) -> bool:
        if self._cal.is_open(now):
            return True
        if self._cal.is_pre_open(now):
            return False
        last = self._cal.last_closed_session(now)
        return last is not None and self._snapshot_session != last

    def poll_once(self) -> bool:
        """One cycle if one is due and not backing off. Returns True when a fetch was tried."""
        if not self._cfg.enabled or not self._poll_lock.acquire(blocking=False):
            return False
        try:
            now = self._clock()
            self._cal.refresh(now)
            with self._lock:
                if self._retry_at is not None and now < self._retry_at:
                    return False
            if not self._should_fetch(now):
                return False
            self._cycle(now)
            return True
        finally:
            self._poll_lock.release()

    def _cycle(self, now: datetime) -> None:
        with self._lock:
            self._last_attempt = now
        provider = self._cfg.provider.upper()
        try:
            if self._feed is None:
                self._feed = self._factory()
            feed = self._feed
            entries = self._universe()
            symbol_keys = {e.symbol.upper(): feed.stock_key(e.symbol, e.isin) for e in entries}
            with self._lock:
                self._symbol_keys, self._keys_built = symbol_keys, True
            self._fetch_indices(feed, now)
            self._fetch_stocks(feed, sorted({k for k in symbol_keys.values() if k}), now)
        except ProviderAuthError:
            self._feed = None  # the next try rebuilds it, re-reading .env
            self._fail(
                "token_needed", now, self._cfg.auth_retry_seconds,
                f"Live feed needs a fresh token: update the {provider} access token in .env "
                "(it is checked again every minute).",
            )  # fmt: skip
            return
        except ProviderRateLimited as exc:
            delay = max(exc.retry_after or 0.0, self._backoff(30.0))
            self._fail("rate_limited", now, delay, f"{provider} rate limit reached")
            return
        except ProviderError as exc:
            self._fail("error", now, self._backoff(self._cfg.poll_interval_seconds),
                       f"{provider} did not answer ({exc})")  # fmt: skip
            return
        except Exception as exc:  # e.g. the universe could not be read
            self._fail("error", now, self._backoff(self._cfg.poll_interval_seconds),
                       f"Live feed error ({type(exc).__name__})")  # fmt: skip
            return
        with self._lock:
            self._covered = sum(1 for k in symbol_keys.values() if k and k in self._quotes)
            self._total = len(symbol_keys)
            self._failures, self._retry_at, self._last_success = 0, None, now
            self._state = "live"
            missing = self._total - self._covered
            self._message = f"{missing} of {self._total} stocks have no price" if missing else None
            last = self._cal.last_closed_session(now)
            settled = last is not None and now >= self._cal.settled_at(last)
            if last is not None and settled and not self._cal.is_open(now):
                self._snapshot_session = last  # the closing price is in; polling can stop

    def _backoff(self, base: float) -> float:
        with self._lock:
            return float(min(self._cfg.max_backoff_seconds, base * (2**self._failures)))

    def _fail(self, state: str, now: datetime, delay: float, message: str) -> None:
        with self._lock:
            self._failures += 1
            self._state, self._message = state, f"{message}; retrying in {int(delay)} s."
            if state == "token_needed":
                self._message = message
            self._retry_at = now + timedelta(seconds=delay)

    def _pause(self, feed: LiveFeed) -> None:
        self._sleep(feed.min_call_gap_seconds)

    def _fetch_indices(self, feed: LiveFeed, now: datetime) -> None:
        keys = {
            s.id: feed.index_key(s)
            for s in self._cfg.indices
            if self._bad_keys.get(feed.index_key(s), now) <= now
        }
        with self._lock:
            self._index_keys.update({s.id: feed.index_key(s) for s in self._cfg.indices})
        if not keys:
            return
        try:
            got = feed.fetch(list(keys.values()), now, index=True)
        except (ProviderAuthError, ProviderRateLimited):
            raise
        except ProviderError:
            got = {}  # one rejected key must not hide the others: ask one at a time
            for key in keys.values():
                self._pause(feed)
                try:
                    got.update(feed.fetch([key], now, index=True))
                except (ProviderAuthError, ProviderRateLimited):
                    raise
                except ProviderError:
                    self._bad_keys[key] = now + BAD_KEY_RETRY
        with self._lock:
            self._quotes.update(got)
        for spec in self._cfg.indices:
            quote = got.get(keys.get(spec.id, ""))
            if quote is not None:
                self._add_point(feed, spec, quote)

    def _fetch_stocks(self, feed: LiveFeed, keys: list[str], now: datetime) -> None:
        size = max(1, feed.max_batch)
        ok = failed = 0
        last_error: ProviderError | None = None
        for i in range(0, len(keys), size):
            self._pause(feed)
            try:
                got = feed.fetch(keys[i : i + size], now, index=False)
            except (ProviderAuthError, ProviderRateLimited):
                raise  # what arrived before stays in the cache
            except ProviderError as exc:
                failed, last_error = failed + 1, exc
                continue
            ok += 1
            with self._lock:
                self._quotes.update(got)
        if failed and not ok and last_error is not None:
            raise last_error

    def _session_of(self, t: datetime) -> date | None:
        if self._cal.is_open(t):
            return t.astimezone(IST).date()
        return self._cal.last_closed_session(t)

    def _add_point(self, feed: LiveFeed, spec: IndexSpec, quote: LiveQuote) -> None:
        session = self._session_of(quote.fetched_at)
        key = (spec.id, session)
        backfill: list[IntradayPoint] = []
        if key not in self._backfilled:
            self._backfilled.add(key)  # one try per index and session
            try:
                self._pause(feed)
                backfill = feed.intraday(feed.index_key(spec))
            except ProviderError as exc:
                logger.info("index line backfill for %s failed: %s", spec.id, type(exc).__name__)
        with self._lock:
            if self._points_session.get(spec.id) != session:
                self._points[spec.id], self._points_session[spec.id] = {}, session
            pts = self._points[spec.id]
            for p in backfill:
                if self._session_of(p.time) == session:
                    pts[p.time.replace(second=0, microsecond=0)] = p.value
            pts[quote.fetched_at.replace(second=0, microsecond=0)] = quote.last_price
            for old in sorted(pts)[:-MAX_POINTS]:
                del pts[old]

    # ---- what the API reads ---------------------------------------------------------------

    def status(self) -> FeedStatus:
        now = self._clock()
        market = "closed"
        if self._cal.is_open(now):
            market = "open"
        elif self._cal.is_pre_open(now):
            market = "pre_open"
        with self._lock:
            state, message = self._state, self._message
            if not self._cfg.enabled:
                state, message = "disabled", "Live prices are switched off in config/live.yaml"
            elif state not in FAILURE_STATES:
                if self._last_success is None:
                    state = "starting"
                elif market == "open":
                    age = (now - self._last_success).total_seconds()
                    state = "live" if age <= self._cfg.stale_after_seconds else "stale"
                else:
                    state = "closed"
            return FeedStatus(
                state=state, message=message, provider=self._cfg.provider.upper(), market=market,
                last_success_at=self._last_success, retry_at=self._retry_at,
                stocks_covered=self._covered, stocks_total=self._total,
                holidays_known=self._cal.holidays_known,
            )  # fmt: skip

    def _view_of(self, quote: LiveQuote | None, none_reason: str) -> QuoteView:
        if quote is None:
            return QuoteView(None, None, None, None, none_reason)
        now = self._clock()
        session = self._session_of(quote.fetched_at)
        age = max(0, int((now - quote.fetched_at).total_seconds()))
        if self._cal.is_open(now) and session == now.astimezone(IST).date():
            mode = "live" if age <= self._cfg.stale_after_seconds else "stale"
            return QuoteView(quote, mode, session, age, None)
        return QuoteView(quote, "closed", session, None, None)

    def view(self, symbol: str) -> QuoteView:
        with self._lock:
            if not self._cfg.enabled:
                return self._view_of(None, "live prices are switched off")
            if not self._keys_built:
                return self._view_of(None, "waiting for the first live price")
            sym = symbol.upper()
            if sym not in self._symbol_keys:
                return self._view_of(None, "not in the scanned universe")
            key = self._symbol_keys[sym]
            name = self._cfg.provider.upper()
            if key is None:
                return self._view_of(None, f"{name} has no key for this stock")
            return self._view_of(self._quotes.get(key), f"no price from {name}")

    def index_views(self) -> list[IndexView]:
        with self._lock:
            out: list[IndexView] = []
            for spec in self._cfg.indices:
                key = self._index_keys.get(spec.id)
                reason = "waiting for the first live price" if self._last_success is None else (
                    f"no price from {self._cfg.provider.upper()}"
                )  # fmt: skip
                view = self._view_of(self._quotes.get(key) if key else None, reason)
                pts = self._points.get(spec.id, {}) if view.quote else {}
                line = [IntradayPoint(t, v) for t, v in sorted(pts.items())]
                out.append(IndexView(spec, view, line))
            return out
