"""Response models of API v1 (FRONTEND_SPECIFICATION 67.3).

Every response carries ``as_of`` (the session it describes) and ``data_time`` (when the serving
copy was written, IST). A value the database does not have is ``null``, never 0 (AGENTS.md 4).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class Stamped(BaseModel):
    as_of: date | None
    data_time: datetime


class Tier(BaseModel):
    name: str
    grade: int
    ranked: bool


class StrategyInfo(BaseModel):
    strategy_id: str
    algorithm_version: str
    config_hash: str  # first 12 characters
    stage: str
    min_grade: int
    tiers: list[Tier]  # lowest first


class StrategiesResponse(Stamped):
    strategies: list[StrategyInfo]


class StrategyDates(BaseModel):
    strategy_id: str
    latest_scan: date | None
    ledger_through: date | None


class StatusResponse(Stamped):
    prices_date: date | None
    strategies: list[StrategyDates]
    daily_run: str | None  # the last line of daily_runs.log
    newest_backup: str | None
    backup_age_days: float | None
    warnings: list[str]


class SummaryStrategy(BaseModel):
    strategy_id: str
    ranked: int
    grade2_plus: int
    breakouts: int
    mean_top10_score: float | None


class SummaryResponse(Stamped):
    universe_size: int | None
    scanned: int | None
    trend_template_pass: int | None
    strategies: list[SummaryStrategy]


class MarketDay(BaseModel):
    day: date
    breadth_pct: float | None
    index: float | None  # equal-weight index of our universe, 100 at the first day shown
    index_ma50: float | None
    regime_on: bool


class MarketResponse(Stamped):
    label: str
    regime_rule: str
    breadth_threshold_pct: float
    regime_days_on_last_20: int
    days: list[MarketDay]


class SetupRow(BaseModel):
    strategy_id: str
    instrument_id: str
    symbol: str
    company: str | None
    classification: str
    grade: int
    status: str
    confirmation_state: str | None
    eligible: bool
    score: float | None
    trend_score: float | None
    pattern_score: float | None
    volume_score: float | None
    rs_score: float | None
    percentile: float | None
    rs_rank: int | None
    close: float | None
    change_pct: float | None
    pivot: float | None
    pivot_distance_pct: float | None
    stop: float | None
    base_start: date | None
    base_end: date | None
    base_days: int | None
    base_depth_pct: float | None
    breakout_date: date | None


class SetupsResponse(Stamped):
    strategy_id: str
    rows: list[SetupRow]


class OverlapEntry(BaseModel):
    strategy_id: str
    classification: str
    grade: int
    score: float | None


class OverlapRow(BaseModel):
    instrument_id: str
    symbol: str
    company: str | None
    strategies: list[OverlapEntry]


class OverlapResponse(Stamped):
    rows: list[OverlapRow]


class HealthItem(BaseModel):
    id: str
    label: str
    status: str  # green | amber | red | grey (grey: not enough data to read)
    text: str
    value: float | None
    score: float | None  # 0-100, a straight-line score for the verdict; None: not scored (grey)
    short: str  # the reading in a few words for the card; the full sentence is `text`


class HealthVerdict(BaseModel):
    score: int  # 0-100, the average of the scored readings
    label: str
    status: str  # green | amber | orange | red
    override: bool  # the index is below its 200-day average: Downtrend whatever the score
    green: int
    amber: int
    red: int
    counted: int  # readings that carried a score
    weakest: list[str]
    strongest: list[str]


class HealthGroup(BaseModel):
    id: str
    title: str
    items: list[HealthItem]


class HealthPoint(BaseModel):
    day: date
    index: float  # VCP Universe Index, 100 at the first chart day
    ma50: float | None
    ma200: float | None
    highs: int
    lows: int
    above50: float | None
    above200: float | None
    ad_line: float
    ad_ma50: float | None


class HealthTrade(BaseModel):
    day: date
    ret_pct: float


class MarketHealthResponse(Stamped):
    groups: list[HealthGroup]
    points: list[HealthPoint]
    trades: list[HealthTrade]
    verdict: HealthVerdict | None


class SearchHit(BaseModel):
    instrument_id: str
    symbol: str
    company: str | None


class SearchResponse(Stamped):
    query: str
    results: list[SearchHit]


class ScreenerRow(BaseModel):
    instrument_id: str
    symbol: str
    company: str | None
    stage: str | None
    trend_template_pass: bool | None
    conditions_passed: int | None
    conditions_total: int | None
    near_52w_high: bool | None
    rs_rank: int | None
    trend_score: float | None
    close: float | None
    change_pct: float | None
    classification: str | None
    grade: int | None
    status: str | None
    score: float | None
    pivot: float | None
    pivot_distance_pct: float | None
    eligible: bool | None


class ScreenerResponse(Stamped):
    strategy_id: str
    scanned: int
    total: int
    page: int
    page_size: int
    stage_counts: dict[str, int]
    rows: list[ScreenerRow]


class Bar(BaseModel):
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None


class BarsResponse(Stamped):
    symbol: str
    company: str | None
    adjusted: bool
    bars: list[Bar]


class Point(BaseModel):
    label: str
    day: date
    price: float | None


class Contraction(BaseModel):
    sequence: int
    peak_date: date | None
    peak_price: float | None
    trough_date: date | None
    trough_price: float | None
    depth_pct: float | None


class ScorePart(BaseModel):
    component: str
    sub_component: str
    raw: float | None
    normalized: float | None
    points: float | None
    max_points: float | None


class Condition(BaseModel):
    name: str
    measurement: float | None
    threshold: float | None
    passed: bool | None


class StockSetup(BaseModel):
    strategy_id: str
    classification: str
    grade: int
    status: str
    eligible: bool
    score: float | None
    pivot: float | None
    stop: float | None
    base_start: date | None
    base_end: date | None
    base_depth_pct: float | None
    breakout_date: date | None
    points: list[Point]
    contractions: list[Contraction]
    score_parts: list[ScorePart]
    details: dict[str, Any]


class StockSetupsResponse(Stamped):
    symbol: str
    company: str | None
    close: float | None
    weekly_stage: str | None
    weekly_stage2_pass: bool | None
    trend_template_pass: bool | None
    conditions: list[Condition]
    setups: list[StockSetup]


class ActivityEvent(BaseModel):
    day: date
    time: datetime | None  # null when only the day is known
    kind: str
    strategy_id: str | None
    symbol: str | None
    text: str


class ActivityResponse(Stamped):
    events: list[ActivityEvent]


class OpenPosition(BaseModel):
    instrument_id: str
    symbol: str
    entry_day: date
    entry: float
    stop: float | None
    last: float | None
    open_pct: float | None


class Criterion(BaseModel):
    name: str
    required: str
    value: float | None
    met: bool | None  # null: not judged yet (no data, or fewer than 30 closed trades)


class PaperStrategy(BaseModel):
    strategy_id: str
    ledger_through: date | None
    closed: int
    win_rate_pct: float | None
    avg_return_pct: float | None
    profit_factor: float | None
    open: list[OpenPosition]
    skipped_no_slot: int
    divergences: int
    criteria: list[Criterion]


class PaperResponse(Stamped):
    rule_set: str
    review_from: date
    min_closed_trades: int
    open_positions: int
    strategies: list[PaperStrategy]
