// Response contracts of API v1 (src/vcp_scanner/api/models.py). A response that does not match
// is rejected, so the page never shows a half-understood number. Missing values are null.
import { z } from "zod";

const num = z.number().nullable();
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const dayN = day.nullable();
const stamp = { as_of: dayN, data_time: z.string() };

export const tier = z.object({ name: z.string(), grade: z.number(), ranked: z.boolean() });

export const strategies = z.object({
  ...stamp,
  strategies: z.array(
    z.object({
      strategy_id: z.string(),
      algorithm_version: z.string(),
      config_hash: z.string(),
      stage: z.string(),
      min_grade: z.number(),
      tiers: z.array(tier),
    }),
  ),
});

export const status = z.object({
  ...stamp,
  prices_date: dayN,
  strategies: z.array(
    z.object({ strategy_id: z.string(), latest_scan: dayN, ledger_through: dayN }),
  ),
  daily_run: z.string().nullable(),
  newest_backup: z.string().nullable(),
  // the newest evening run as scripts/daily_run.sh recorded it; absent from older APIs
  run: z
    .object({
      state: z.enum(["running", "finished", "stopped"]),
      started: z.string(),
      step: z.string().nullable(),
      step_since: z.string().nullable(),
      exit_code: z.number().nullable(),
    })
    .nullable()
    .optional(),
  backup_age_days: num,
  warnings: z.array(z.string()),
});

export const summary = z.object({
  ...stamp,
  universe_size: z.number().nullable(),
  scanned: z.number().nullable(),
  trend_template_pass: z.number().nullable(),
  strategies: z.array(
    z.object({
      strategy_id: z.string(),
      ranked: z.number(),
      grade2_plus: z.number(),
      breakouts: z.number(),
      mean_top10_score: num,
    }),
  ),
});

export const marketDay = z.object({
  day,
  breadth_pct: num,
  index: num,
  index_ma50: num,
  regime_on: z.boolean(),
});

export const market = z.object({
  ...stamp,
  label: z.string(),
  regime_rule: z.string(),
  breadth_threshold_pct: z.number(),
  regime_days_on_last_20: z.number(),
  days: z.array(marketDay),
});

export const setupRow = z.object({
  strategy_id: z.string(),
  instrument_id: z.string(),
  symbol: z.string(),
  company: z.string().nullable(),
  classification: z.string(),
  grade: z.number(),
  status: z.string(),
  confirmation_state: z.string().nullable(),
  eligible: z.boolean(),
  score: num,
  trend_score: num,
  pattern_score: num,
  volume_score: num,
  rs_score: num,
  percentile: num,
  rs_rank: num,
  close: num,
  change_pct: num,
  pivot: num,
  pivot_distance_pct: num,
  stop: num,
  base_start: dayN,
  base_end: dayN,
  base_days: num,
  base_depth_pct: num,
  breakout_date: dayN,
});

export const setups = z.object({ ...stamp, strategy_id: z.string(), rows: z.array(setupRow) });

export const overlap = z.object({
  ...stamp,
  rows: z.array(
    z.object({
      instrument_id: z.string(),
      symbol: z.string(),
      company: z.string().nullable(),
      strategies: z.array(
        z.object({
          strategy_id: z.string(),
          classification: z.string(),
          grade: z.number(),
          score: num,
        }),
      ),
    }),
  ),
});

export const bar = z.object({
  day,
  open: num,
  high: num,
  low: num,
  close: num,
  volume: num,
  sma20: num,
  sma50: num,
  sma200: num,
});

export const bars = z.object({
  ...stamp,
  symbol: z.string(),
  company: z.string().nullable(),
  adjusted: z.boolean(),
  bars: z.array(bar),
});

export const point = z.object({ label: z.string(), day, price: num });
export const contraction = z.object({
  sequence: z.number(),
  peak_date: dayN,
  peak_price: num,
  trough_date: dayN,
  trough_price: num,
  depth_pct: num,
});
export const scorePart = z.object({
  component: z.string(),
  sub_component: z.string(),
  raw: num,
  normalized: num,
  points: num,
  max_points: num,
});
export const condition = z.object({
  name: z.string(),
  measurement: num,
  threshold: num,
  passed: z.boolean().nullable(),
});

export const stockSetup = z.object({
  strategy_id: z.string(),
  classification: z.string(),
  grade: z.number(),
  status: z.string(),
  eligible: z.boolean(),
  score: num,
  pivot: num,
  stop: num,
  base_start: dayN,
  base_end: dayN,
  base_depth_pct: num,
  breakout_date: dayN,
  points: z.array(point),
  contractions: z.array(contraction),
  score_parts: z.array(scorePart),
  details: z.record(z.unknown()),
});

export const stockSetups = z.object({
  ...stamp,
  symbol: z.string(),
  company: z.string().nullable(),
  sector: z.string().nullable().optional(),
  industry: z.string().nullable().optional(),
  sector_source: z.string().nullable().optional(),
  sector_since: z.string().nullable().optional(),
  close: num,
  weekly_stage: z.string().nullable(),
  weekly_stage2_pass: z.boolean().nullable(),
  trend_template_pass: z.boolean().nullable(),
  conditions: z.array(condition),
  setups: z.array(stockSetup),
});

export const activityEvent = z.object({
  day,
  time: z.string().nullable(),
  kind: z.string(),
  strategy_id: z.string().nullable(),
  symbol: z.string().nullable(),
  text: z.string(),
});
export const activity = z.object({ ...stamp, events: z.array(activityEvent) });

export const openPosition = z.object({
  instrument_id: z.string(),
  symbol: z.string(),
  entry_day: day,
  entry: z.number(),
  stop: num,
  last: num,
  open_pct: num,
});
export const criterion = z.object({
  name: z.string(),
  required: z.string(),
  value: num,
  met: z.boolean().nullable(),
});
export const paperStrategy = z.object({
  strategy_id: z.string(),
  ledger_through: dayN,
  closed: z.number(),
  win_rate_pct: num,
  avg_return_pct: num,
  profit_factor: num,
  open: z.array(openPosition),
  skipped_no_slot: z.number(),
  divergences: z.number(),
  criteria: z.array(criterion),
});
export const paper = z.object({
  ...stamp,
  rule_set: z.string(),
  review_from: day,
  min_closed_trades: z.number(),
  open_positions: z.number(),
  strategies: z.array(paperStrategy),
});

export const ipoRow = z.object({
  instrument_id: z.string(),
  symbol: z.string(),
  company: z.string().nullable(),
  listing_date: day,
  bars: z.number(),
  ipo_open: num,
  ipo_close: num,
  first_day_high: num,
  close: num,
  change_pct: num,
  since_listing_pct: num,
  vs_first_day_high_pct: num,
  high_since_listing: num,
  from_high_pct: num,
  sma20: num,
  sma50: num,
  vs_sma20_pct: num,
  vs_sma50_pct: num,
  range_pct: num,
  avg_traded_value: num,
});
export const ipos = z.object({ ...stamp, full_history_bars: z.number(), rows: z.array(ipoRow) });

export const backtestStats = z.object({
  trades: num,
  win_rate_pct: num,
  avg_return_pct: num,
  median_return_pct: num,
  profit_factor: num,
  avg_win_pct: num,
  avg_loss_pct: num,
  avg_hold_days: num,
});
export const backtestPortfolio = backtestStats.extend({
  total_return_pct: num,
  cagr_pct: num,
  max_drawdown_pct: num,
  sharpe: num,
  avg_exposure_pct: num,
  skipped: num,
});
export const backtestRun = z.object({
  backtest_id: z.string(),
  strategy_id: z.string(),
  algorithm_version: z.string().nullable(),
  period: z.string().nullable(),
  start_date: day,
  end_date: day,
  survivorship: z.string().nullable(),
  entry: z.string().nullable(),
  regime: z.string().nullable(),
  rule: z.string().nullable(),
  cost_bps: num,
  max_positions: num,
  classes: z.array(z.string()),
  min_score: num,
  baseline: z.boolean(),
  paper_rules: z.boolean(),
  completed_at: z.string().nullable(),
  every_trade: backtestStats,
  portfolio: backtestPortfolio,
});
export const backtests = z.object({
  ...stamp,
  available: z.boolean(),
  reason: z.string().nullable(),
  runs: z.array(backtestRun),
});
export const reportFile = z.object({
  kind: z.enum(["daily", "weekly"]),
  name: z.string(),
  label: z.string(),
  size_bytes: z.number(),
  modified: z.string(),
});
export const reports = z.object({ ...stamp, files: z.array(reportFile) });

export const search = z.object({
  ...stamp,
  query: z.string(),
  results: z.array(
    z.object({ instrument_id: z.string(), symbol: z.string(), company: z.string().nullable() }),
  ),
});

export const screenerRow = z.object({
  instrument_id: z.string(),
  symbol: z.string(),
  company: z.string().nullable(),
  stage: z.string().nullable(),
  trend_template_pass: z.boolean().nullable(),
  conditions_passed: z.number().nullable(),
  conditions_total: z.number().nullable(),
  near_52w_high: z.boolean().nullable(),
  rs_rank: z.number().nullable(),
  trend_score: num,
  close: num,
  change_pct: num,
  classification: z.string().nullable(),
  grade: z.number().nullable(),
  status: z.string().nullable(),
  score: num,
  pivot: num,
  pivot_distance_pct: num,
  eligible: z.boolean().nullable(),
  in_universe: z.boolean(),
  outside_reason: z.string().nullable(),
  sector: z.string().nullable().optional(),
  eps_yoy: num.optional(),
  sales_yoy: num.optional(),
});

export const screener = z.object({
  ...stamp,
  strategy_id: z.string(),
  scanned: z.number(),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
  stage_counts: z.record(z.string(), z.number()),
  rows: z.array(screenerRow),
});

export const healthItem = z.object({
  id: z.string(),
  label: z.string(),
  status: z.enum(["green", "amber", "red", "grey"]),
  text: z.string(),
  value: num,
  score: num,
  short: z.string(),
});

export const marketHealth = z.object({
  ...stamp,
  groups: z.array(z.object({ id: z.string(), title: z.string(), items: z.array(healthItem) })),
  warnings: z.array(z.object({ id: z.string(), text: z.string() })),
  new_highs: z.array(
    z.object({
      symbol: z.string(),
      rs_rank: num,
      sector: z.string().nullable(),
      close: num.optional(),
      change_pct: num.optional(),
      volume_ratio: num.optional(),
      vs_sma50_pct: num.optional(),
      trend_template: z.boolean().nullable().optional(),
      stage: z.string().nullable().optional(),
    }),
  ),
  sectors: z.array(
    z.object({
      sector: z.string().nullable(),
      stocks: z.number(),
      above50_pct: num,
      above50_change: num,
      median_rs: num,
      breakouts: z.number(),
      new_highs: z.number(),
      index_name: z.string().nullable(),
      index_change_pct: num,
      index_uptrend: z.array(z.boolean().nullable()),
    }),
  ),
  rotation: z.array(
    z.object({
      sector: z.string(),
      index_name: z.string(),
      quadrant: z.enum(["leading", "weakening", "lagging", "improving"]),
      points: z.array(z.object({ day: z.string(), ratio: z.number(), momentum: z.number() })),
    }),
  ),
  trades: z.array(z.object({ day: z.string(), ret_pct: z.number() })),
  stages: z.array(
    z.object({
      day: z.string(),
      stage1: z.number(),
      stage2: z.number(),
      stage3: z.number(),
      stage4: z.number(),
      transition: z.number(),
      total: z.number(),
    }),
  ),
  flow: z.array(
    z.object({
      day: z.string(),
      new_setups: num,
      breakouts: z.number(),
      failed_pct: num,
      judged: z.number(),
      failed: z.number(),
    }),
  ),
  /** the score's own readings per session, for the cards' charts (FRONTEND_SPECIFICATION §67.34) */
  score_points: z.array(
    z.object({
      day: z.string(),
      nifty500: num,
      n500_ma50: num,
      n500_ma150: num,
      n500_ma200: num,
      nifty50: num,
      smallcap250: num,
      vqi: num,
      kind: z.string().nullable(),
      dist25: num,
      acc25: num,
      above50: num,
      above150: num,
      above200: num,
      ad_ratio: num,
      ad_line: num,
      thrust_ratio: num,
      thrust: z.boolean(),
      net_highs: num,
      highs_share: num,
      tt_share: num,
      leaders: num,
    }),
  ),
  /** Market Health Score 2 (MARKET_HEALTH_SPECIFICATION); null until a day is stored */
  score: z
    .object({
      day: z.string(),
      settings_version: z.string(),
      raw: num,
      raw_label: z.string().nullable(),
      label: z.string().nullable(),
      complete: z.boolean(),
      exposure: z.string().nullable(),
      pillars: z.array(
        z.object({
          id: z.string(),
          title: z.string(),
          weight: z.number(),
          score: num,
          contribution: num,
          note: z.string(),
          parts: z.array(z.object({ id: z.string(), label: z.string(), reading: num, points: num, weight: z.number() })),
        }),
      ),
      caps: z.array(z.object({ id: z.string(), max: z.string(), reason: z.string() })),
      missing: z.array(z.string()),
      last_thrust: z.string().nullable(),
      judged: z.number(),
      history: z.array(z.object({ day: z.string(), raw: num, raw_label: z.string().nullable(), label: z.string().nullable(), thrust: z.boolean() })),
    })
    .nullable(),
});

export type Strategies = z.infer<typeof strategies>;
export type Status = z.infer<typeof status>;
export type Summary = z.infer<typeof summary>;
export type Market = z.infer<typeof market>;
export type SetupRow = z.infer<typeof setupRow>;
export type Setups = z.infer<typeof setups>;
export type Overlap = z.infer<typeof overlap>;
export type Bar = z.infer<typeof bar>;
export type Bars = z.infer<typeof bars>;
export type StockSetup = z.infer<typeof stockSetup>;
export type StockSetups = z.infer<typeof stockSetups>;
export type ActivityEvent = z.infer<typeof activityEvent>;
export type Activity = z.infer<typeof activity>;
export type PaperStrategy = z.infer<typeof paperStrategy>;
export type Paper = z.infer<typeof paper>;
export type BacktestRun = z.infer<typeof backtestRun>;
export type Backtests = z.infer<typeof backtests>;
export type ReportFile = z.infer<typeof reportFile>;
export type Reports = z.infer<typeof reports>;
export type IpoRow = z.infer<typeof ipoRow>;
export type Ipos = z.infer<typeof ipos>;
export type Search = z.infer<typeof search>;
export type ScreenerRow = z.infer<typeof screenerRow>;
export type Screener = z.infer<typeof screener>;
export type MarketHealth = z.infer<typeof marketHealth>;

// ---- Fundamentals (F5), display only: they never change a score or the paper ledger ------------
// Amounts in rupees; growth and margins in %; acceleration and expansion in percentage points.
// `statuses` says why a null metric is null (MISSING, NEGATIVE_BASE, BASIS_CHANGE, NOT_APPLICABLE).
const fundamentalFields = {
  view_date: day,
  period_end: day,
  available_at: z.string().nullable(),
  basis: z.string(),
  revenue: num,
  eps: num,
  ttm_eps: num,
  operating_margin: num,
  net_margin: num,
  revenue_qoq: num,
  eps_yoy: num,
  eps_qoq: num,
  sales_yoy: num,
  eps_acceleration: num,
  margin_expansion: num,
  roe: num,
  debt_to_equity: num,
  statuses: z.record(z.string()),
  availability: z.number(),
  quarters_available: z.number(),
  stale: z.boolean(),
  stale_after: day,
  estimated: z.boolean(),
  restated: z.boolean(),
  gate_pass: z.boolean(),
  gate_reason: z.string().nullable(),
};
export const fundamentalSummary = z.object(fundamentalFields);

export const fundamentalQuarter = z.object({
  period_end: day,
  available_at: z.string(),
  basis: z.string(),
  revision: z.number(),
  estimated: z.boolean(),
  revenue: num,
  eps: num,
  eps_as_filed: num,
  operating_margin: num,
  net_margin: num,
  eps_yoy: num,
  eps_yoy_status: z.string(),
  sales_yoy: num,
  sales_yoy_status: z.string(),
});

/** One quarter-end shareholding pattern (F8), % of the company's shares. */
export const shareholdingQuarter = z.object({
  as_on: z.string(),
  available_at: z.string(),
  revision: z.number(),
  layout: z.string(),
  promoter_pct: z.number(),
  promoter_pledged_pct: num,
  fii_pct: z.number(),
  fdi_pct: num,
  dii_pct: z.number(),
  mf_pct: z.number(),
  insurance_pct: z.number(),
  government_pct: z.number(),
  non_institutions_pct: z.number(),
  public_pct: z.number(),
  other_pct: z.number(),
  shareholders: num,
});

export const shareholdingSummary = shareholdingQuarter.extend({
  promoter_change_1q: num,
  promoter_change_4q: num,
  fii_change_1q: num,
  fii_change_4q: num,
  dii_change_1q: num,
  dii_change_4q: num,
  pledged_change_1q: num,
  pledge_rising: z.boolean(),
  shareholders_change_1q_pct: num,
  stale: z.boolean(),
});

export const stockFundamentals = z.object({
  ...stamp,
  symbol: z.string(),
  company: z.string().nullable(),
  summary: fundamentalSummary.nullable(),
  quarters: z.array(fundamentalQuarter),
  reason: z.string().nullable(),
  shareholding: shareholdingSummary.nullable().optional(),
  shareholding_quarters: z.array(shareholdingQuarter).optional(),
});

export const fundamentalsRow = z.object({
  ...fundamentalFields,
  instrument_id: z.string(),
  symbol: z.string(),
  company: z.string().nullable(),
  sector: z.string().nullable(),
  rs_rank: z.number().nullable(),
  // shareholding (F8), the latest quarter end; null: not filed
  shareholding_as_on: z.string().nullable().optional(),
  shareholding_stale: z.boolean().nullable().optional(),
  promoter_pct: num.optional(),
  promoter_change_1q: num.optional(),
  fii_pct: num.optional(),
  fii_change_1q: num.optional(),
  dii_pct: num.optional(),
  dii_change_1q: num.optional(),
  pledged_pct: num.optional(),
  pledge_rising: z.boolean().nullable().optional(),
});

export const fundamentals = z.object({
  ...stamp,
  universe: z.number(),
  covered: z.number(),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
  rows: z.array(fundamentalsRow),
});

export type FundamentalSummary = z.infer<typeof fundamentalSummary>;
export type FundamentalQuarter = z.infer<typeof fundamentalQuarter>;
export type StockFundamentals = z.infer<typeof stockFundamentals>;
export type ShareholdingQuarter = z.infer<typeof shareholdingQuarter>;
export type ShareholdingSummary = z.infer<typeof shareholdingSummary>;
export type FundamentalsRow = z.infer<typeof fundamentalsRow>;
export type Fundamentals = z.infer<typeof fundamentals>;

// ---- Live prices (display only), src/vcp_scanner/api/live_routes.py ----------------------------
// `as_of` is the session the prices belong to, `data_time` when the newest was received, `mode`
// whether they are live, stale or the close. A price the feed does not have is null with a reason.
export const liveFeed = z.object({
  state: z.enum(["disabled", "starting", "live", "stale", "closed", "token_needed", "rate_limited", "error"]),
  message: z.string().nullable(),
  provider: z.string(),
  market: z.enum(["open", "pre_open", "closed"]),
  last_success_at: z.string().nullable(),
  retry_at: z.string().nullable(),
  stocks_covered: num,
  stocks_total: num,
  holidays_known: z.boolean(),
});

const liveStamp = {
  ...stamp,
  mode: z.enum(["live", "stale", "closed", "unavailable"]),
  feed: liveFeed,
};

export const liveQuote = z.object({
  symbol: z.string(),
  available: z.boolean(),
  reason: z.string().nullable(),
  mode: z.enum(["live", "stale", "closed"]).nullable(),
  session_date: dayN,
  source: z.string().nullable(),
  last_price: num,
  prev_close: num,
  change: num,
  change_pct: num,
  open: num,
  high: num,
  low: num,
  volume: num,
  exchange_time: z.string().nullable(),
  fetched_at: z.string().nullable(),
  delay_seconds: num,
});

export const liveQuotes = z.object({ ...liveStamp, quotes: z.array(liveQuote) });

export const liveIndex = liveQuote.extend({
  id: z.string(),
  label: z.string(),
  points: z.array(z.object({ t: z.string(), v: z.number() })),
});

export const liveIndices = z.object({ ...liveStamp, indices: z.array(liveIndex) });
export const liveStatus = z.object(liveStamp);

export type LiveFeed = z.infer<typeof liveFeed>;
export type LiveQuote = z.infer<typeof liveQuote>;
export type LiveQuotes = z.infer<typeof liveQuotes>;
export type LiveIndex = z.infer<typeof liveIndex>;
export type LiveIndices = z.infer<typeof liveIndices>;
export type LiveStatus = z.infer<typeof liveStatus>;
