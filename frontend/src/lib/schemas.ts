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
  points: z.array(
    z.object({
      day: z.string(),
      index: z.number(),
      ma50: num,
      ma200: num,
      highs: z.number(),
      lows: z.number(),
      above50: num,
      above200: num,
      ad_line: z.number(),
      ad_ma50: num,
    }),
  ),
  trades: z.array(z.object({ day: z.string(), ret_pct: z.number() })),
  verdict: z
    .object({
      score: z.number(),
      label: z.string(),
      status: z.enum(["green", "amber", "orange", "red"]),
      override: z.boolean(),
      green: z.number(),
      amber: z.number(),
      red: z.number(),
      counted: z.number(),
      weakest: z.array(z.string()),
      strongest: z.array(z.string()),
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
export type Search = z.infer<typeof search>;
export type ScreenerRow = z.infer<typeof screenerRow>;
export type Screener = z.infer<typeof screener>;
export type MarketHealth = z.infer<typeof marketHealth>;
