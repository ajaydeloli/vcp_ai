"""``/api/v1/live/*``: live prices for display (FRONTEND_SPECIFICATION 67.19). GET only.

The endpoints read the in-memory cache of ``vcp_scanner.live``; they never call a provider
themselves, so page loads cannot add provider calls. Nothing here touches a scan, a score, a label,
the paper ledger or any database (the universe is a read-only list from the serving copy).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query

from vcp_scanner.api import models as m
from vcp_scanner.live.service import FeedStatus, LiveService, QuoteView

MAX_SYMBOLS = 500


def _feed(s: FeedStatus) -> m.LiveFeedInfo:
    return m.LiveFeedInfo(
        state=s.state,
        message=s.message, provider=s.provider,
        market=s.market,
        last_success_at=s.last_success_at, retry_at=s.retry_at, stocks_covered=s.stocks_covered,
        stocks_total=s.stocks_total, holidays_known=s.holidays_known,
    )  # fmt: skip


def _quote(symbol: str, v: QuoteView, source: str) -> m.LiveQuoteOut:
    q = v.quote
    if q is None:
        return m.LiveQuoteOut(
            symbol=symbol, available=False, reason=v.reason, mode=None, session_date=None,
            source=None, last_price=None, prev_close=None, change=None, change_pct=None,
            open=None, high=None, low=None, volume=None, exchange_time=None, fetched_at=None,
            delay_seconds=None,
        )  # fmt: skip
    return m.LiveQuoteOut(
        symbol=symbol, available=True, reason=None,
        mode=v.mode,
        session_date=v.session_date, source=source, last_price=q.last_price,
        prev_close=q.prev_close, change=q.change, change_pct=q.change_pct, open=q.open,
        high=q.high, low=q.low, volume=q.volume, exchange_time=q.exchange_time,
        fetched_at=q.fetched_at, delay_seconds=v.delay_seconds,
    )  # fmt: skip


Mode = Literal["live", "stale", "closed", "unavailable"]


def _stamp(
    quotes: list[m.LiveQuoteOut], feed: m.LiveFeedInfo, now: datetime
) -> tuple[m.LiveFeedInfo, Mode, datetime, date | None]:
    modes = {q.mode for q in quotes if q.available}
    mode: Mode = "unavailable"
    for candidate in ("live", "stale", "closed"):
        if candidate in modes:
            mode = candidate
            break
    fetched = [q.fetched_at for q in quotes if q.fetched_at is not None]
    sessions = [q.session_date for q in quotes if q.session_date is not None]
    return feed, mode, max(fetched, default=now), max(sessions, default=None)


def _symbols(raw: str) -> list[str]:
    seen: dict[str, None] = {}
    for part in raw.split(","):
        sym = part.strip().upper()
        if sym:
            seen.setdefault(sym)
    if not seen:
        raise HTTPException(422, "symbols must list at least one symbol")
    if len(seen) > MAX_SYMBOLS:
        raise HTTPException(422, f"at most {MAX_SYMBOLS} symbols per request")
    return list(seen)


def add_live_routes(
    app: FastAPI, svc: LiveService, clock: Callable[[], datetime], api: str = "/api/v1"
) -> None:
    @app.get(f"{api}/live/status", response_model=m.LiveStatusResponse)
    def live_status() -> m.LiveStatusResponse:
        svc.touch()
        now = clock()
        return m.LiveStatusResponse(
            as_of=None, data_time=now, mode="unavailable", feed=_feed(svc.status())
        )

    @app.get(f"{api}/live/quotes", response_model=m.LiveQuotesResponse)
    def live_quotes(
        symbols: Annotated[str, Query(description="comma-separated symbols, at most 500")],
    ) -> m.LiveQuotesResponse:
        wanted = _symbols(symbols)
        svc.touch()
        source = svc.status().provider
        quotes = [_quote(s, svc.view(s), source) for s in wanted]
        feed, mode, data_time, as_of = _stamp(quotes, _feed(svc.status()), clock())
        return m.LiveQuotesResponse(
            as_of=as_of, data_time=data_time, mode=mode, feed=feed, quotes=quotes
        )

    @app.get(f"{api}/live/indices", response_model=m.LiveIndicesResponse)
    def live_indices() -> m.LiveIndicesResponse:
        svc.touch()
        status = svc.status()
        out: list[m.LiveIndexOut] = []
        for iv in svc.index_views():
            base = _quote(iv.spec.id, iv.view, status.provider)
            out.append(
                m.LiveIndexOut(
                    **base.model_dump(),
                    id=iv.spec.id,
                    label=iv.spec.label,
                    points=[m.IndexPoint(t=p.time, v=p.value) for p in iv.points],
                )  # fmt: skip
            )
        feed, mode, data_time, as_of = _stamp(list(out), _feed(status), clock())
        return m.LiveIndicesResponse(
            as_of=as_of, data_time=data_time, mode=mode, feed=feed, indices=out
        )
