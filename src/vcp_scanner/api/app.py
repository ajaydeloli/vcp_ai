"""The read-only dashboard API (FRONTEND_SPECIFICATION 67.3): GET endpoints under ``/api/v1``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from vcp_scanner.api import health, reports
from vcp_scanner.api import models as m
from vcp_scanner.api import queries as q
from vcp_scanner.api.context import Context, StrategySpec, build_context
from vcp_scanner.api.db import ServingCopyMissing, ServingDb
from vcp_scanner.api.live_routes import add_live_routes
from vcp_scanner.data.providers._time import IST
from vcp_scanner.live.config import load_live_config
from vcp_scanner.live.factory import make_feed_factory, nse_holiday_loader
from vcp_scanner.live.hours import MarketCalendar
from vcp_scanner.live.models import UniverseEntry
from vcp_scanner.live.service import LiveService
from vcp_scanner.serving import default_serving_path

MARKET_LABEL = "VCP Quality Index, VQI (equal-weight index of the stocks we scan), not NIFTY"
#: ``?date=YYYY-MM-DD`` (module level: FastAPI resolves annotations in the module namespace).
DateParam = Annotated[date | None, Query(alias="date")]
ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def create_app(
    serving_path: str | Path | None = None, config_dir: str | Path = "config",
    data_dir: str | Path = "data", now: Callable[[], datetime] | None = None,
    origins: tuple[str, ...] = ORIGINS, live: LiveService | None = None, env_file: str = ".env",
) -> FastAPI:  # fmt: skip
    """``serving_path`` defaults to ``<data_dir>/serving/vcp_serving.duckdb``; logs and backups
    are read from ``data_dir``. ``live`` is the live-price service (display only); by default one
    is built from ``config/live.yaml`` and reads credentials from ``env_file`` at its first poll."""
    data = Path(data_dir)
    db = ServingDb(serving_path or default_serving_path(data / "vcp_scanner.duckdb"))
    ctx: Context = build_context(config_dir, data)
    clock = now or (lambda: datetime.now(IST))
    app = FastAPI(title="VCP scanner dashboard API", version="1")
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_methods=["GET"])

    @app.exception_handler(ServingCopyMissing)
    def _missing(_: Request, exc: ServingCopyMissing) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def spec_or_404(strategy: str) -> StrategySpec:
        spec = ctx.strategy(strategy)
        if spec is None:
            known = ", ".join(s.strategy_id for s in ctx.strategies)
            raise HTTPException(404, f"unknown strategy {strategy!r}; strategies: {known}")
        return spec

    def newest_scan(cur: q.Cur) -> date | None:
        return max((d for s in ctx.strategies if (d := q.latest_scan(cur, s))), default=None)

    api = "/api/v1"

    def live_universe() -> list[UniverseEntry]:
        def read() -> list[UniverseEntry]:
            with db.cursor() as cur:
                return [UniverseEntry(sym, isin) for sym, isin in q.live_universe(cur, ctx)]

        result: list[UniverseEntry] = db.cached("live_universe", read)
        return result

    if live is None:
        live_cfg = load_live_config(config_dir)
        live = LiveService(
            live_cfg, make_feed_factory(live_cfg, env_file), live_universe,
            MarketCalendar(nse_holiday_loader(data)), clock,
        )  # fmt: skip
    add_live_routes(app, live, clock, api)

    def stock_or_404(cur: q.Cur, symbol: str) -> tuple[str, str, str | None]:
        found = q.resolve_symbol(cur, symbol)
        if found is None:
            raise HTTPException(404, f"unknown symbol {symbol!r}")
        return found

    @app.get(f"{api}/status", response_model=m.StatusResponse)
    def status() -> m.StatusResponse:
        with db.cursor() as cur:
            return reports.status(cur, ctx, clock(), db.data_time())

    @app.get(f"{api}/strategies", response_model=m.StrategiesResponse)
    def strategies() -> m.StrategiesResponse:
        infos = [
            m.StrategyInfo(strategy_id=s.strategy_id, algorithm_version=s.algorithm_version,
                           config_hash=s.config_hash[:12], stage=s.stage, min_grade=s.min_grade,
                           tiers=q.tiers(s))
            for s in ctx.strategies
        ]  # fmt: skip
        with db.cursor() as cur:
            return m.StrategiesResponse(as_of=newest_scan(cur), data_time=db.data_time(),
                                        strategies=infos)  # fmt: skip

    @app.get(f"{api}/summary", response_model=m.SummaryResponse)
    def summary(on: DateParam = None) -> m.SummaryResponse:
        with db.cursor() as cur:
            day = on or newest_scan(cur)
            if day is None:
                return m.SummaryResponse(
                    as_of=None, data_time=db.data_time(), universe_size=None, scanned=None,
                    trend_template_pass=None, strategies=[],
                )  # fmt: skip
            return q.summary(cur, ctx, day, db.data_time())

    def market_series(end: date, days: int) -> list[m.MarketDay]:
        with db.cursor() as cur:
            return q.market_days(cur, ctx, end, days)

    @app.get(f"{api}/market", response_model=m.MarketResponse)
    def market(days: Annotated[int, Query(ge=20, le=1000)] = 250) -> m.MarketResponse:
        with db.cursor() as cur:
            end = q.latest_prices_date(cur)
        rows: list[m.MarketDay] = (
            db.cached(("market", days), lambda: market_series(end, days)) if end else []
        )
        return m.MarketResponse(
            as_of=end, data_time=db.data_time(), label=MARKET_LABEL, regime_rule=ctx.regime_rule,
            breadth_threshold_pct=40.0,
            regime_days_on_last_20=sum(d.regime_on for d in rows[-20:]), days=rows,
        )  # fmt: skip

    @app.get(f"{api}/market/health", response_model=m.MarketHealthResponse)
    def market_health() -> m.MarketHealthResponse:
        """The market read through price action, leadership, breadth and our own paper trades.
        Display only: nothing here feeds the regime rule or any strategy."""
        with db.cursor() as cur:
            end = q.latest_prices_date(cur)
            if end is None:
                return m.MarketHealthResponse(
                    as_of=None,
                    data_time=db.data_time(),
                    groups=[],
                    points=[],
                    trades=[],
                    verdict=None,
                    index_days=None,
                )
            result: m.MarketHealthResponse = db.cached(
                ("health", end), lambda: health.market_health(cur, ctx, end, db.data_time())
            )
            return result

    @app.get(f"{api}/setups/overlap", response_model=m.OverlapResponse)
    def setups_overlap(on: DateParam = None) -> m.OverlapResponse:
        with db.cursor() as cur:
            day = on or newest_scan(cur)
            rows = q.overlap(cur, ctx, day) if day else []
            return m.OverlapResponse(as_of=day, data_time=db.data_time(), rows=rows)

    @app.get(f"{api}/setups", response_model=m.SetupsResponse)
    def setups(
        strategy: str = "vcp", on: DateParam = None,
        min_grade: Annotated[int | None, Query(ge=0, le=3)] = None, status: str | None = None,
        eligible: bool = True, limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    ) -> m.SetupsResponse:  # fmt: skip
        spec = spec_or_404(strategy)
        with db.cursor() as cur:
            day = on or q.latest_scan(cur, spec)
            rows = (
                q.setup_rows(cur, ctx, spec, day, eligible=eligible, min_grade=min_grade,
                             status=status, limit=limit)
                if day else []
            )  # fmt: skip
            return m.SetupsResponse(as_of=day, data_time=db.data_time(), strategy_id=strategy,
                                    rows=rows)  # fmt: skip

    @app.get(f"{api}/screener", response_model=m.ScreenerResponse)
    def screener(
        strategy: str = "vcp", on: DateParam = None,
        q_: Annotated[str | None, Query(alias="q", max_length=40)] = None,
        symbols: Annotated[str | None, Query(max_length=2000)] = None,
        stage: Annotated[list[str] | None, Query()] = None,
        tt_pass: bool | None = None, near_high: bool | None = None,
        min_rs: Annotated[int | None, Query(ge=0, le=99)] = None,
        min_conditions: Annotated[int | None, Query(ge=0, le=10)] = None,
        has_setup: bool = False, min_grade: Annotated[int | None, Query(ge=0, le=3)] = None,
        status: Annotated[str | None, Query(max_length=40)] = None,
        classification: Annotated[str | None, Query(max_length=40)] = None,
        min_strategies: Annotated[int | None, Query(ge=2, le=10)] = None,
        sort: Annotated[str, Query(pattern="^(" + "|".join(q.SCREENER_SORTS) + ")$")] = "rs_rank",
        direction: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
        page: Annotated[int, Query(ge=1, le=1000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> m.ScreenerResponse:  # fmt: skip
        spec = spec_or_404(strategy)
        f = SimpleNamespace(
            q=q_, symbols={x.strip().upper() for x in (symbols or "").split(",") if x.strip()},
            stages=stage or [], tt_pass=tt_pass, near_high=near_high, min_rs=min_rs,
            min_conditions=min_conditions, has_setup=has_setup, min_grade=min_grade,
            status=status, classification=classification, min_strategies=min_strategies,
            sort=sort, descending=direction == "desc", page=page,
            page_size=page_size,
        )  # fmt: skip
        with db.cursor() as cur:
            day = on or q.screener_day(cur, ctx)
            if day is None:
                return m.ScreenerResponse(
                    as_of=None, data_time=db.data_time(), strategy_id=strategy, scanned=0,
                    total=0, page=page, page_size=page_size, stage_counts={}, rows=[],
                )  # fmt: skip
            return q.screener(cur, ctx, spec, day, f, db.data_time())

    @app.get(f"{api}/search", response_model=m.SearchResponse)
    def search(
        q_: Annotated[str, Query(alias="q", min_length=1, max_length=40)],
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
    ) -> m.SearchResponse:
        with db.cursor() as cur:
            hits = q.search_symbols(cur, q_, limit)
            return m.SearchResponse(as_of=q.latest_prices_date(cur), data_time=db.data_time(),
                                    query=q_, results=hits)  # fmt: skip

    @app.get(f"{api}/stocks/{{symbol}}/bars", response_model=m.BarsResponse)
    def stock_bars(
        symbol: str, days: Annotated[int, Query(ge=20, le=1500)] = 260
    ) -> m.BarsResponse:
        with db.cursor() as cur:
            iid, sym, company = stock_or_404(cur, symbol)
            rows = q.bars(cur, iid, days)
            return m.BarsResponse(
                as_of=rows[-1].day if rows else None, data_time=db.data_time(), symbol=sym,
                company=company, adjusted=True, bars=rows,
            )  # fmt: skip

    @app.get(f"{api}/stocks/{{symbol}}/setups", response_model=m.StockSetupsResponse)
    def stock_setups(symbol: str, on: DateParam = None) -> m.StockSetupsResponse:
        with db.cursor() as cur:
            iid, sym, company = stock_or_404(cur, symbol)
            day = on or newest_scan(cur)
            if day is None:
                return m.StockSetupsResponse(
                    as_of=None, data_time=db.data_time(), symbol=sym, company=company,
                    close=None, weekly_stage=None, weekly_stage2_pass=None,
                    trend_template_pass=None, conditions=[], setups=[],
                )  # fmt: skip
            return q.stock_setups(cur, ctx, iid, sym, company, day, db.data_time())

    @app.get(f"{api}/stocks/{{symbol}}/history", response_model=m.ActivityResponse)
    def stock_history(
        symbol: str, days: Annotated[int, Query(ge=1, le=400)] = 180
    ) -> m.ActivityResponse:
        """Breakouts and paper-ledger events of one stock (the activity feed cut to a symbol)."""
        with db.cursor() as cur:
            _iid, sym, _company = stock_or_404(cur, symbol)
            end = q.latest_prices_date(cur) or clock().date()
            feed = reports.activity(cur, ctx, end, days, db.data_time())
            mine = [e for e in feed.events if e.symbol == sym]
            return m.ActivityResponse(as_of=feed.as_of, data_time=feed.data_time, events=mine)

    @app.get(f"{api}/activity", response_model=m.ActivityResponse)
    def activity(days: Annotated[int, Query(ge=1, le=60)] = 7) -> m.ActivityResponse:
        with db.cursor() as cur:
            end = q.latest_prices_date(cur) or clock().date()
            return reports.activity(cur, ctx, end, days, db.data_time())

    @app.get(f"{api}/paper", response_model=m.PaperResponse)
    def paper() -> m.PaperResponse:
        with db.cursor() as cur:
            return reports.paper(cur, ctx, db.data_time())

    return app
