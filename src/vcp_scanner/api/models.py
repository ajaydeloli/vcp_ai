"""Response models of API v1 (FRONTEND_SPECIFICATION 67.3).

Every response carries ``as_of`` (the session it describes) and ``data_time`` (when the serving
copy was written, IST). A value the database does not have is ``null``, never 0 (AGENTS.md 4).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

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


class DailyRunProgress(BaseModel):
    """The newest evening run as scripts/daily_run.sh recorded it (vcp_scanner.run_state)."""

    state: str  # running | finished | stopped
    started: datetime
    step: str | None  # the step running, or the last one reached
    step_since: str | None  # HH:MM IST
    exit_code: int | None


class StatusResponse(Stamped):
    prices_date: date | None
    strategies: list[StrategyDates]
    daily_run: str | None  # the last line of daily_runs.log
    run: DailyRunProgress | None = None  # None before the first run that recorded it
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


class HealthScorePart(BaseModel):
    id: str
    label: str
    reading: float | None  # in the unit the label names; None: not measured
    points: float | None  # 0-100
    weight: float  # inside the pillar


class HealthScorePillar(BaseModel):
    id: str  # index_trend | breadth | leadership | accumulation | breakouts
    title: str
    weight: float  # the pillar's weight in the settings
    score: float | None  # 0-100; None: missing (left out, the other weights rescaled)
    contribution: float | None  # points it adds to the raw score, after rescaling
    note: str
    parts: list[HealthScorePart]


class HealthScoreCap(BaseModel):
    id: str
    max: str  # the best label the day can have
    reason: str


class HealthScoreDay(BaseModel):
    day: date
    raw: float | None
    raw_label: str | None
    label: str | None  # after the caps
    thrust: bool


class HealthScorePoint(BaseModel):
    """One session of the score's own readings, for the cards' charts (FRONTEND_SPECIFICATION
    §67.34). Indices are rebased to 100 at the first session shown; NIFTY 500's averages use
    its factor. None where a reading cannot be measured."""

    day: date
    nifty500: float | None
    n500_ma50: float | None
    n500_ma150: float | None
    n500_ma200: float | None
    nifty50: float | None
    smallcap250: float | None
    vqi: float | None
    kind: str | None  # NIFTY 500 distribution | accumulation day
    dist25: int | None  # NIFTY 500 distribution days in the last 25 sessions
    acc25: int | None  # NIFTY 500 accumulation days in the last 25 sessions
    above50: float | None
    above150: float | None
    above200: float | None
    ad_ratio: float | None  # net advances over 20 sessions, share of all moves
    ad_line: float | None
    thrust_ratio: float | None
    thrust: bool
    net_highs: float | None  # new 52-week highs - new lows that day
    highs_share: float | None  # highs / (highs + lows), 10-session averages, %
    tt_share: float | None
    leaders: int | None


class HealthScore(BaseModel):
    """Market Health Score 2 (MARKET_HEALTH_SPECIFICATION): display, research and a shadow
    traffic light only."""

    day: date
    settings_version: str
    raw: float | None
    raw_label: str | None
    label: str | None  # after the caps
    complete: bool
    exposure: str | None  # shadow traffic light: full | half | none
    pillars: list[HealthScorePillar]
    caps: list[HealthScoreCap]
    missing: list[str]
    last_thrust: date | None  # the latest breadth thrust in the history shown
    judged: int  # breakouts judged behind the breakout readings
    history: list[HealthScoreDay]  # oldest first


class HealthGroup(BaseModel):
    id: str
    title: str
    items: list[HealthItem]


class HealthStagePoint(BaseModel):
    """Stocks of the latest scan in each weekly stage at the end of one week."""

    day: date
    stage1: int
    stage2: int
    stage3: int
    stage4: int
    transition: int
    total: int  # stocks with a stage that week (the five counts add up to this)


class HealthFlowPoint(BaseModel):
    """Setups and breakouts of one session, all five strategies together (display only)."""

    day: date
    new_setups: int | None  # eligible today, not on the strategy's previous scan; None: no scan
    breakouts: int  # breakout events dated this session
    failed_pct: float | None  # failed share of the breakouts judged in the last FLOW_WINDOW
    judged: int  # breakouts judged in that window (None rate below MIN_JUDGED)
    failed: int = 0  # of which failed


class HealthWarning(BaseModel):
    """A divergence: the index near its high while a breadth line is not (display only)."""

    id: str  # ad_divergence | highs_divergence
    text: str


class HealthNewHigh(BaseModel):
    symbol: str
    rs_rank: int | None
    sector: str | None
    close: float | None = None  # adjusted close on the day
    change_pct: float | None = None  # the day's change, %
    volume_ratio: float | None = None  # volume vs the 50 sessions before
    vs_sma50_pct: float | None = None  # close vs its 50-day average, %
    trend_template: bool | None = None  # passes the Trend Template on the latest scan
    stage: str | None = None  # weekly stage on the latest scan (STAGE_1..STAGE_4, TRANSITION)


class HealthSector(BaseModel):
    """One NSE sector of today's universe (display only). ``sector`` None: not classified yet."""

    sector: str | None
    stocks: int  # universe stocks of the sector with a 50-day average today
    above50_pct: float | None
    above50_change: float | None  # percentage points against SECTOR_MONTH sessions ago
    median_rs: float | None
    breakouts: int  # stocks with a breakout (any strategy) in the last SECTOR_MONTH sessions
    new_highs: int  # stocks at a new 52-week high today
    index_name: str | None  # NSE's sector index, when there is one
    index_change_pct: float | None  # its change over SECTOR_MONTH sessions
    # in an uptrend by the score's test (above its 50-day, up over the horizon), 1 / 3 / 6 months
    index_uptrend: list[bool | None] = []


class HealthRotationPoint(BaseModel):
    day: date  # the last session of the week
    ratio: float  # RS-Ratio: 100 x (sector / NIFTY 500) / its 10-week average
    momentum: float  # RS-Momentum: 100 x RS-Ratio / RS-Ratio 4 weeks earlier


class HealthRotation(BaseModel):
    """One NSE sector index on the relative rotation graph (display only)."""

    sector: str
    index_name: str
    quadrant: str  # leading | weakening | lagging | improving (of the latest point)
    points: list[HealthRotationPoint]  # oldest first, up to 12 weeks


class HealthTrade(BaseModel):
    day: date
    ret_pct: float


class MarketHealthResponse(Stamped):
    groups: list[HealthGroup]
    trades: list[HealthTrade]
    score: HealthScore | None = None  # Market Health Score 2; None until a day is stored
    score_points: list[HealthScorePoint] = []  # the score's readings per session, for the cards
    stages: list[HealthStagePoint] = []
    flow: list[HealthFlowPoint] = []  # from the first stored scan on
    warnings: list[HealthWarning] = []
    new_highs: list[HealthNewHigh] = []  # universe stocks at a new 52-week high on as_of
    sectors: list[HealthSector] = []  # strongest first; not classified last
    rotation: list[HealthRotation] = []  # sector indices vs NIFTY 500, weekly


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
    in_universe: bool = True
    outside_reason: str | None = None  # why a stock outside the universe is outside
    sector: str | None = None  # NSE sector (S1); None: not classified yet
    # Fundamentals (F5), display and filter only: the stock's view as of the scan day.
    eps_yoy: float | None = None
    sales_yoy: float | None = None


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
    sector: str | None = None
    industry: str | None = None
    sector_source: str | None = None  # NSE_INDEX_LIST | NSE_QUOTE | MANUAL
    sector_since: date | None = None  # the day the current sector was recorded
    close: float | None
    weekly_stage: str | None
    weekly_stage2_pass: bool | None
    trend_template_pass: bool | None
    conditions: list[Condition]
    setups: list[StockSetup]
    # prices re-adjusted (corporate action entered) after this scan: its RS, Trend Template and
    # setups for the stock used the old factors and are wrong until the next scan
    rescaled_after_scan: bool = False


class ActivityEvent(BaseModel):
    day: date
    time: datetime | None  # null when only the day is known
    kind: str
    strategy_id: str | None
    symbol: str | None
    text: str


class ActivityResponse(Stamped):
    events: list[ActivityEvent]


class ScoreHistoryRow(BaseModel):
    """One stock's setup on one scan day in one strategy (the stored scan, not recomputed)."""

    day: date
    strategy_id: str
    classification: str
    grade: int
    status: str
    eligible: bool
    score: float | None
    pivot: float | None


class ScoreHistoryResponse(Stamped):
    symbol: str
    since: date | None
    rows: list[ScoreHistoryRow]


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


# ---- Live prices (FRONTEND_SPECIFICATION 67.19): display only, from the in-memory feed ----------


class LiveFeedInfo(BaseModel):
    state: Literal[
        "disabled", "starting", "live", "stale", "closed", "token_needed", "rate_limited", "error"
    ]
    message: str | None  # what to tell the person: needs a fresh token, rate limited, ...
    provider: str  # UPSTOX or KITE, one config setting
    market: Literal["open", "pre_open", "closed"]
    last_success_at: datetime | None
    retry_at: datetime | None
    stocks_covered: int | None  # stocks that have a price / stocks in the scanned universe
    stocks_total: int | None
    holidays_known: bool  # False: the NSE holiday list is not loaded, weekdays only


class LiveStamped(BaseModel):
    """``as_of`` is the session the prices belong to, ``data_time`` when the newest was
    received (IST); ``mode`` says whether they are live, stale or the close."""

    as_of: date | None
    data_time: datetime
    mode: Literal["live", "stale", "closed", "unavailable"]
    feed: LiveFeedInfo


class LiveQuoteOut(BaseModel):
    symbol: str
    available: bool
    reason: str | None  # why there is no price ("not available", never 0)
    mode: Literal["live", "stale", "closed"] | None
    session_date: date | None
    source: str | None  # the provider this price came from
    last_price: float | None
    prev_close: float | None
    change: float | None
    change_pct: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: int | None
    exchange_time: datetime | None
    fetched_at: datetime | None
    delay_seconds: int | None  # seconds since the price was received (live and stale only)


class LiveQuotesResponse(LiveStamped):
    quotes: list[LiveQuoteOut]


class IndexPoint(BaseModel):
    t: datetime
    v: float


class LiveIndexOut(LiveQuoteOut):
    id: str
    label: str
    points: list[IndexPoint]  # today's intraday line, empty when there is none


class LiveIndicesResponse(LiveStamped):
    indices: list[LiveIndexOut]


class LiveStatusResponse(LiveStamped):
    pass


class IpoRow(BaseModel):
    """A recently listed main-board stock; a measure that needs more bars than it has is None."""

    instrument_id: str
    symbol: str
    company: str | None
    listing_date: date
    bars: int
    ipo_open: float | None
    ipo_close: float | None
    first_day_high: float | None
    close: float | None
    change_pct: float | None
    since_listing_pct: float | None
    vs_first_day_high_pct: float | None
    high_since_listing: float | None
    from_high_pct: float | None
    sma20: float | None
    sma50: float | None
    vs_sma20_pct: float | None
    vs_sma50_pct: float | None
    range_pct: float | None  # high-low range of the last 10 bars as a percent of the close
    avg_traded_value: float | None  # mean close x volume over the last (up to) 20 bars


class IposResponse(Stamped):
    full_history_bars: int  # stocks with at least this many bars are in the scan universe
    rows: list[IpoRow]


# ---- Backtest runs and report files (FRONTEND_SPECIFICATION 67.26, 67.27) ----------------------


class BacktestStats(BaseModel):
    trades: int | None
    win_rate_pct: float | None
    avg_return_pct: float | None
    median_return_pct: float | None
    profit_factor: float | None
    avg_win_pct: float | None
    avg_loss_pct: float | None
    avg_hold_days: float | None


class BacktestPortfolio(BacktestStats):
    total_return_pct: float | None
    cagr_pct: float | None
    max_drawdown_pct: float | None
    sharpe: float | None
    avg_exposure_pct: float | None
    skipped: int | None


class BacktestRun(BaseModel):
    backtest_id: str
    strategy_id: str
    algorithm_version: str | None
    period: str | None  # development | validation
    start_date: date
    end_date: date
    survivorship: str | None
    entry: str | None
    regime: str | None
    rule: str | None
    cost_bps: float | None
    max_positions: int | None
    classes: list[str]
    min_score: float | None
    baseline: bool
    paper_rules: bool  # entry, regime, exit rule, positions and costs of the frozen paper rules
    completed_at: datetime | None
    every_trade: BacktestStats
    portfolio: BacktestPortfolio


class BacktestsResponse(Stamped):
    available: bool
    reason: str | None  # why there are no runs to show
    runs: list[BacktestRun]


class ReportFile(BaseModel):
    kind: Literal["daily", "weekly"]
    name: str
    label: str
    size_bytes: int
    modified: datetime


class ReportsResponse(Stamped):
    files: list[ReportFile]


class ReportRequest(BaseModel):
    """The one thing the API writes: a report file (FRONTEND_SPECIFICATION 67.27)."""

    kind: Literal["daily", "weekly"]
    date: date  # a trading day (daily), or any day of the week (weekly)


# --- fundamentals (F5; display only, FUNDAMENTALS_SPECIFICATION §2 and §11) --------------------


class FundamentalSummary(BaseModel):
    """A stock's fundamental view as of a date: the stored row with the greatest date on or
    before it. Amounts in rupees; growth and margins in %; ``*_acceleration`` / ``*_expansion``
    in percentage points. ``statuses`` says why a NULL metric is NULL (MISSING, NEGATIVE_BASE,
    BASIS_CHANGE, NOT_APPLICABLE)."""

    view_date: date  # the date this view first applied (a filing became usable)
    period_end: date  # the latest quarter
    available_at: datetime | None  # when that quarter's filing was published (IST)
    basis: str  # CONSOLIDATED | STANDALONE
    revenue: float | None
    eps: float | None
    ttm_eps: float | None
    operating_margin: float | None
    net_margin: float | None
    revenue_qoq: float | None
    eps_yoy: float | None
    eps_qoq: float | None
    sales_yoy: float | None
    eps_acceleration: float | None
    margin_expansion: float | None
    roe: float | None
    debt_to_equity: float | None
    statuses: dict[str, str]
    availability: float  # share of the seven metrics present, 0..1
    quarters_available: int
    stale: bool  # the latest quarter is older than the staleness limit on the asked date
    stale_after: date
    estimated: bool  # the latest quarter was derived from year-to-date values
    restated: bool
    gate_pass: bool  # shown only, never applied (§2)
    gate_reason: str | None


class FundamentalQuarter(BaseModel):
    period_end: date
    available_at: datetime  # publication (broadcast) time, IST
    basis: str
    revision: int
    estimated: bool
    revenue: float | None
    eps: float | None  # on the latest share basis (splits and bonuses applied)
    eps_as_filed: float | None
    operating_margin: float | None
    net_margin: float | None
    eps_yoy: float | None
    eps_yoy_status: str
    sales_yoy: float | None
    sales_yoy_status: str


class ShareholdingQuarter(BaseModel):
    """One quarter-end shareholding pattern (F8; % of the company's shares unless noted)."""

    as_on: date  # the quarter end
    available_at: datetime  # broadcast time, IST
    revision: int
    layout: str  # NEW | SPLIT | OLD (the XBRL layout; the pledge basis changes with NEW)
    promoter_pct: float
    promoter_pledged_pct: float | None  # % of the promoter group's own shares
    fii_pct: float  # foreign portfolio investors
    fdi_pct: float | None  # foreign direct investment, not part of FII
    dii_pct: float
    mf_pct: float  # part of DII
    insurance_pct: float  # part of DII
    government_pct: float
    non_institutions_pct: float  # retail and others
    public_pct: float
    other_pct: float  # neither promoter nor public (employee trusts)
    shareholders: int | None


class ShareholdingSummary(ShareholdingQuarter):
    """The latest quarter with its changes in percentage points; None: that quarter is
    missing (N/A, never 0)."""

    promoter_change_1q: float | None
    promoter_change_4q: float | None
    fii_change_1q: float | None
    fii_change_4q: float | None
    dii_change_1q: float | None
    dii_change_4q: float | None
    pledged_change_1q: float | None  # None also across the June 2025 layout change
    pledge_rising: bool
    shareholders_change_1q_pct: float | None  # % change in the number of shareholders
    stale: bool  # the latest quarter end is more than 120 days old


class StockFundamentalsResponse(Stamped):
    symbol: str
    company: str | None
    summary: FundamentalSummary | None  # None: no usable results filed by ``as_of``
    quarters: list[FundamentalQuarter]  # oldest first
    reason: str | None  # why there is no summary
    # Shareholding pattern (F8), display only.
    shareholding: ShareholdingSummary | None = None
    shareholding_quarters: list[ShareholdingQuarter] = []  # oldest first, up to 8


class FundamentalsRow(FundamentalSummary):
    instrument_id: str
    symbol: str
    company: str | None
    sector: str | None
    rs_rank: int | None
    # Shareholding (F8), the latest quarter end as of ``as_of``; None: not filed (N/A).
    shareholding_as_on: date | None = None
    shareholding_stale: bool | None = None
    promoter_pct: float | None = None
    promoter_change_1q: float | None = None
    fii_pct: float | None = None
    fii_change_1q: float | None = None
    dii_pct: float | None = None
    dii_change_1q: float | None = None
    pledged_pct: float | None = None  # % of the promoter group's own shares
    pledge_rising: bool | None = None


class FundamentalsResponse(Stamped):
    universe: int  # stocks of the scan universe on ``as_of``
    covered: int  # of which with fundamentals
    total: int  # after the filters
    page: int
    page_size: int
    rows: list[FundamentalsRow]


# --- diagnostics (AGENT_SPECIFICATION A1.5) ------------------------------------------------


class DataQualityCount(BaseModel):
    event_type: str
    severity: str
    open: int
    blocking: int  # open events that stop signals for the stock


class DataQualityEvent(BaseModel):
    event_id: str
    symbol: str
    trade_date: date | None
    event_type: str
    severity: str
    blocks_signal: bool
    status: str
    description: str | None
    resolved_by: str | None
    resolution_note: str | None
    context: Any = None


class DataQualityResponse(Stamped):
    open_counts: list[DataQualityCount]
    total: int  # events matching the filters, before the limit
    events: list[DataQualityEvent]


class CorporateActionRow(BaseModel):
    action_type: str
    ex_date: date | None
    ratio_numerator: float | None
    ratio_denominator: float | None
    cash_amount: float | None
    status: str  # CONFIRMED, SINGLE_SOURCE, MANUAL_OVERRIDE, PROVIDER_CONFLICT, ...
    conflict_fields: str | None
    resolved_by: str | None


class AdjustmentRow(BaseModel):
    effective_date: date
    price_factor: float | None
    volume_factor: float | None
    cumulative_price_factor: float | None
    source: str | None


class PriceGap(BaseModel):
    day: date
    prev_close: float | None
    open: float | None
    close: float | None
    gap_pct: float
    adjusted: bool  # a price factor takes effect that day


class CorporateActionsResponse(Stamped):
    symbol: str
    gap_threshold_pct: float
    actions: list[CorporateActionRow]
    adjustments: list[AdjustmentRow]
    events: list[DataQualityEvent]
    gaps: list[PriceGap]


class PriceRow(BaseModel):
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    close_adj: float | None
    factor: float | None  # close_adj / close
    provider: str | None
    data_status: str | None


class PricesResponse(Stamped):
    symbol: str
    rows: list[PriceRow]


class ScanRunRow(BaseModel):
    strategy_id: str | None
    scan_type: str
    status: str
    config_hash: str | None
    code_commit: str | None
    code_dirty: bool | None
    survivorship: str | None
    counts: Any = None
    versions: Any = None
    started_at: datetime | None
    completed_at: datetime | None


class ScanRunsResponse(Stamped):
    run_summary: str | None  # that day's line of logs/daily_runs.log
    log_warnings: list[str]  # warning and error lines of that day's evening run
    runs: list[ScanRunRow]


class BacktestGroup(BaseModel):
    key: str
    trades: int
    avg_ret: float | None
    win_rate: float | None


class BacktestTrade(BaseModel):
    symbol: str
    entry_day: date
    entry: float | None
    exit_day: date | None
    exit: str  # STOP, TIME_EXIT, TRAIL_EXIT, EXIT_SIGNAL, OPEN_AT_END
    exit_price: float | None
    ret_pct: float | None


class BacktestDetailResponse(Stamped):
    run: BacktestRun
    trades_total: int
    by_exit: list[BacktestGroup]
    by_year: list[BacktestGroup]
    trades: list[BacktestTrade]  # newest entries first, cut to the limit


# --- the agent page (AGENT_SPECIFICATION A3 stage 2) --------------------------------------


class AgentProposal(BaseModel):
    proposal_id: int
    created_at: datetime
    kind: str  # corporate_action | resolve_event | sector | watchlist | candidate
    title: str
    change: Any
    rationale: str
    evidence: str
    status: str  # BUILDING | BUILD_FAILED | PENDING | APPROVED | REJECTED | APPLIED | FAILED
    decided_by: str | None
    decided_at: datetime | None
    comment: str | None
    result: str | None
    approve_command: str | None  # to copy into the owner's terminal (pending only)
    reject_command: str | None


class AgentWatch(BaseModel):
    symbol: str
    since: datetime
    proposal_id: int
    reason: str


class WhatIfMetrics(BaseModel):
    """One score variant against the frozen score on the same setups (agent/whatif.py)."""

    days: int | None
    setups: int | None
    ic: float | None  # mean within-day rank correlation with the later return
    ic_t: float | None
    top_per_day_pct: float | None  # mean return of the top N per day
    top_minus_bottom_fifth_pct: float | None
    all_mean_pct: float | None  # mean return of every setup (the reference)
    baseline_ic: float | None
    baseline_ic_t: float | None
    baseline_top_per_day_pct: float | None
    baseline_top_minus_bottom_fifth_pct: float | None
    top_kept_share: float | None  # share of the baseline's top picks the variant keeps
    horizon: int | None
    top_n: int | None


class StudyGroup(BaseModel):
    group: str
    n: int | None
    mean_ret_pct: float | None
    win_rate: float | None
    small_sample: bool = False


class AgentRun(BaseModel):
    run_id: int
    kind: str  # study | backtest | what_if
    window: str
    start: date
    end: date
    ok: bool | None
    summary: str | None
    error: str | None
    change: str | None = None  # what the run changed, in words ("pattern 60, rs 0")
    what_if: WhatIfMetrics | None = None
    study_overall: StudyGroup | None = None
    study_groups: list[StudyGroup] = []
    backtest_id: str | None = None


class AgentReview(BaseModel):
    """The research rules the page shows next to the lab (AGENT_SPECIFICATION, owner rules)."""

    cycle: str
    review_from: date
    days_to_review: int
    max_per_strategy: int
    promotion_min_trades: int
    promotion_min_months: int


class AgentHypothesis(BaseModel):
    hypothesis_id: int
    created_at: datetime
    strategy: str
    title: str
    metric: str
    decision_rule: str
    status: str  # OPEN | CONCLUDED
    outcome: str | None
    conclusion: str | None
    runs: list[AgentRun]


class AgentNote(BaseModel):
    note_id: int
    created_at: datetime
    kind: str
    text: str
    source: str


class CandidateSide(BaseModel):
    """One ledger over a candidate's days (agent/candidates.compare)."""

    closed: int
    open: int
    win_rate: float | None
    avg_ret: float | None
    profit_factor: float | None
    total_ret: float
    max_drawdown: float


class CandidateShadow(BaseModel):
    start_date: date
    days: int
    frozen: CandidateSide
    candidate: CandidateSide
    avg_ret_diff: float | None  # candidate minus frozen, % per trade (5+ trades a side)
    avg_ret_diff_lo: float | None  # 95 % range
    avg_ret_diff_hi: float | None
    ready_to_judge: bool  # 30 shadow trades and 3 months
    needs: list[str]


class CandidateDiffRow(BaseModel):
    date: date
    symbol: str
    change: str  # ADDED | DROPPED | RECLASSIFIED | RESCORED
    frozen_class: str | None
    candidate_class: str | None
    frozen_score: float | None
    candidate_score: float | None


class AgentCandidate(BaseModel):
    """A strategy candidate (AGENT_SPECIFICATION step A4): its changes, the scan diff against
    the frozen scan and, once started, the shadow ledger against the frozen ledger."""

    candidate_id: str
    strategy_id: str
    base_version: str
    candidate_version: str
    status: str  # proposed | active | rejected | retired
    proposal_id: int
    hypothesis_id: int | None
    start_date: date | None
    changes: list[dict[str, Any]]  # file, path, old, new
    scan_diff: dict[str, Any] | None  # days, changes per kind, stocks, frozen eligible
    diff_rows: list[CandidateDiffRow]
    shadow: CandidateShadow | None
    shadow_error: str | None = None


class AgentResponse(Stamped):
    available: bool
    reason: str | None
    pending: int
    proposals: list[AgentProposal]
    ai_watchlist: list[AgentWatch]
    budget_left: dict[str, int]
    hypotheses: list[AgentHypothesis]
    notes: list[AgentNote]
    review: AgentReview | None = None
    candidates: list[AgentCandidate] = []


# --- the alerts page (2026-10-21) ---------------------------------------------------------


class AlertSettings(BaseModel):
    evening_near_pivot_pct: float
    intraday_near_pivot_pct: float
    approach_pct: float
    chase_warn_pct: float
    poll_seconds: int
    include_ai_watchlist: bool
    own_symbols: list[str]


class AlertWatcher(BaseModel):
    running: bool  # checked within the last 20 minutes
    last_check: datetime | None
    market: str | None  # open | closed | ... as the live feed reports it
    error: str | None


class AlertWatched(BaseModel):
    symbol: str
    pivot: float
    strategy: str
    why: str  # setup | AI watchlist | your list
    last_price: float | None
    gap_pct: float | None  # + above the pivot, - below


class AlertSent(BaseModel):
    sent_at: datetime
    kind: str  # EVENING | APPROACHING | CROSSED | TEST
    symbol: str | None
    ok: bool
    error: str | None
    text: str


class AlertsResponse(Stamped):
    enabled: bool
    telegram_configured: bool  # yes/no only: the token is never sent
    evening_enabled: bool
    intraday_enabled: bool
    settings: AlertSettings
    watcher: AlertWatcher
    watching: list[AlertWatched]
    sent: list[AlertSent]


# --- Trend Template page (owner, 2026-10-21) ------------------------------------------------


class TTConditionCount(BaseModel):
    condition_id: int
    name: str
    passed: int  # stocks passing this condition
    only_fail: int  # stocks failing this condition and no other (it alone keeps them out)


class TTFail(BaseModel):
    name: str
    measurement: float | None
    threshold: float | None


class TTStock(BaseModel):
    symbol: str
    company: str | None
    sector: str | None
    stage: str | None
    rs_rank: int | None
    close: float | None
    failed: list[TTFail]  # the conditions failed on the day shown (empty for a pass)
    previous_stage: str | None = None


class TTStageRow(BaseModel):
    sector: str  # "All" for the whole population
    counts: dict[str, int]  # weekly stage -> stocks
    total: int


class TrendTemplateResponse(Stamped):
    previous: date | None  # the scan day before, for new / dropped
    week_ago: date | None  # the scan day the stage moves are measured from
    population: int  # stocks with enough data
    passing: int
    passing_previous: int | None
    conditions: list[TTConditionCount]
    new_passers: list[TTStock]
    dropped: list[TTStock]
    nearly: list[TTStock]  # failing exactly one condition, strongest RS first
    nearly_total: int
    stages: list[TTStageRow]
    into_stage2: list[TTStock]  # weekly stage 2 now, not at week_ago


# ---- Past Leaders (owner, 2026-10-23): the stored superperformance study ----------------------


class LeaderRow(BaseModel):
    """One 50 %+ run of the stored study (research and display only)."""

    run_id: int
    symbol: str
    sector: str | None  # today's classification
    launch_date: date  # the low the run started from
    launch_close: float  # adjusted
    peak_date: date
    peak_close: float
    gain_pct: float
    gain_band: str  # 50-100 | doubled (100-199) | tripled (200+)
    sessions_to_peak: int
    complete: bool  # the full 252 sessions after the launch are known
    max_pullback_pct: float  # deepest close below a high inside the run
    to50: int | None  # sessions from the launch to +50 %
    launch_stage: str | None
    launch_rs: int | None
    launch_tt: bool | None
    launch_dist_high_pct: float | None  # % below the 52-week high on the launch day
    breakout_date: date | None  # first breakout after the launch
    base_length: int | None  # sessions, the base before that breakout
    base_depth_pct: float | None
    vcp_before_breakout: str | None  # VCP | VCP_LIKE | NONE; null = no scan covered it
    bases_count: int  # further bases up to the peak
    giveback_pct: float | None  # deepest close below the peak in the 252 sessions after it
    giveback_of_gain_pct: float | None
    giveback_final: bool  # the whole give-back window is known


class LeadersStudy(BaseModel):
    study_id: str
    created_at: datetime
    study_version: str
    data_end: date
    lift_end: date | None  # last launch day with 252 sessions after it
    survivorship_status: str
    survivorship_detail: str | None
    vcp_source: str
    runs_total: int  # runs in the study, before the filters


class LeadersResponse(Stamped):
    study: LeadersStudy | None  # None: no study stored yet
    years: list[int]  # launch years in the study, for the filter
    sectors: list[str]  # sectors in the study ("Unclassified" for none)
    runs: list[LeaderRow]  # newest launch first


class LeaderBase(BaseModel):
    seq: int  # 1 = the first breakout after the launch
    base_start: date  # the base high
    breakout_date: date
    base_high: float
    depth_pct: float
    length: int  # sessions


class LeaderRunResponse(Stamped):
    study_id: str
    run: LeaderRow
    bases: list[LeaderBase]
    bars: list[Bar]  # 40 sessions before the launch to 60 after the peak
