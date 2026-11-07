// Pure helpers for the Chart page: daily bars to weekly or monthly bars, Heikin Ashi, and the
// indicators (SMA, EMA, Bollinger bands, RSI, MACD), all computed in the browser from the
// adjusted daily bars the API serves. No chart library here, so they can be tested directly.
import type { Bar } from "./schemas";

export type Interval = "D" | "W" | "M";

/** One bar of the chart: a session, a week or a month. ``day`` is its first session. */
export type OBar = { day: string; open: number; high: number; low: number; close: number; volume: number | null };

/** The value of a series per bar (null until enough bars exist). */
export type Series = (number | null)[];

/** Daily API bars with a full OHLC, as chart bars. */
export function dailyBars(bars: Bar[]): OBar[] {
  const out: OBar[] = [];
  for (const b of bars) {
    if (b.open === null || b.high === null || b.low === null || b.close === null) continue;
    out.push({ day: b.day, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume });
  }
  return out;
}

/** The Monday of the ISO week of ``day`` (YYYY-MM-DD), as YYYY-MM-DD. */
export function weekKey(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  const dow = (d.getUTCDay() + 6) % 7; // Monday 0
  d.setUTCDate(d.getUTCDate() - dow);
  return d.toISOString().slice(0, 10);
}

const monthKey = (day: string) => day.slice(0, 7);

/** Daily bars grouped into weeks (Monday to Friday) or calendar months: first open, highest
 *  high, lowest low, last close, summed volume. A bar is dated by its first session, so marks
 *  on any day of the week land on it (``periodOf``). */
export function aggregate(daily: OBar[], interval: Interval): OBar[] {
  if (interval === "D") return daily;
  const key = interval === "W" ? weekKey : monthKey;
  const out: OBar[] = [];
  let cur: OBar | null = null;
  let k = "";
  for (const b of daily) {
    const bk = key(b.day);
    if (cur && bk === k) {
      cur.high = Math.max(cur.high, b.high);
      cur.low = Math.min(cur.low, b.low);
      cur.close = b.close;
      cur.volume = cur.volume === null || b.volume === null ? (cur.volume ?? b.volume) : cur.volume + b.volume;
    } else {
      cur = { ...b };
      k = bk;
      out.push(cur);
    }
  }
  return out;
}

/** For each daily session, the date of the chart bar it belongs to. */
export function periodOf(daily: OBar[], interval: Interval): Map<string, string> {
  const out = new Map<string, string>();
  if (interval === "D") {
    for (const b of daily) out.set(b.day, b.day);
    return out;
  }
  const key = interval === "W" ? weekKey : monthKey;
  let k = "";
  let first = "";
  for (const b of daily) {
    const bk = key(b.day);
    if (bk !== k) {
      k = bk;
      first = b.day;
    }
    out.set(b.day, first);
  }
  return out;
}

/** Heikin Ashi bars: close = OHLC average, open = midpoint of the previous HA bar. */
export function heikinAshi(bars: OBar[]): OBar[] {
  const out: OBar[] = [];
  for (const [i, b] of bars.entries()) {
    const close = (b.open + b.high + b.low + b.close) / 4;
    const prev = out[i - 1];
    const open = prev ? (prev.open + prev.close) / 2 : (b.open + b.close) / 2;
    out.push({ ...b, open, close, high: Math.max(b.high, open, close), low: Math.min(b.low, open, close) });
  }
  return out;
}

export function sma(values: Series, n: number): Series {
  const out: Series = [];
  let sum = 0;
  let count = 0; // values in the window that are not null
  for (let i = 0; i < values.length; i++) {
    const v = values[i] ?? null;
    if (v !== null) {
      sum += v;
      count++;
    }
    if (i >= n) {
      const old = values[i - n] ?? null;
      if (old !== null) {
        sum -= old;
        count--;
      }
    }
    out.push(i >= n - 1 && count === n ? sum / n : null);
  }
  return out;
}

/** Exponential average seeded with the simple average of the first ``n`` values. */
export function ema(values: Series, n: number): Series {
  const out: Series = [];
  const k = 2 / (n + 1);
  let prev: number | null = null;
  let seed: number[] = [];
  for (const v of values) {
    if (v === null) {
      out.push(prev);
      continue;
    }
    if (prev === null) {
      seed.push(v);
      if (seed.length === n) {
        prev = seed.reduce((a, b) => a + b, 0) / n;
        seed = [];
        out.push(prev);
      } else {
        out.push(null);
      }
      continue;
    }
    prev = v * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

/** Bollinger bands: the n-bar average and ``mult`` population standard deviations around it. */
export function bollinger(values: Series, n = 20, mult = 2): { mid: Series; upper: Series; lower: Series } {
  const mid = sma(values, n);
  const upper: Series = [];
  const lower: Series = [];
  for (let i = 0; i < values.length; i++) {
    const m = mid[i] ?? null;
    if (m === null) {
      upper.push(null);
      lower.push(null);
      continue;
    }
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += ((values[j] as number) - m) ** 2;
    const sd = Math.sqrt(s / n);
    upper.push(m + mult * sd);
    lower.push(m - mult * sd);
  }
  return { mid, upper, lower };
}

/** Wilder's RSI over ``n`` bars (0 to 100). */
export function rsi(values: Series, n = 14): Series {
  const out: Series = values.map(() => null);
  let gain = 0;
  let loss = 0;
  for (let i = 1; i < values.length; i++) {
    const a = values[i - 1];
    const b = values[i];
    if (a === null || a === undefined || b === null || b === undefined) return out; // no gaps expected
    const ch = b - a;
    const g = Math.max(ch, 0);
    const l = Math.max(-ch, 0);
    if (i <= n) {
      gain += g;
      loss += l;
      if (i < n) continue;
      gain /= n;
      loss /= n;
    } else {
      gain = (gain * (n - 1) + g) / n;
      loss = (loss * (n - 1) + l) / n;
    }
    out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  }
  return out;
}

/** MACD (fast EMA − slow EMA), its signal EMA, and the histogram (MACD − signal). */
export function macd(values: Series, fast = 12, slow = 26, signal = 9): { macd: Series; signal: Series; hist: Series } {
  const f = ema(values, fast);
  const s = ema(values, slow);
  const line: Series = f.map((v, i) => (v === null || s[i] === null || s[i] === undefined ? null : v - (s[i] as number)));
  const firstIdx = line.findIndex((v) => v !== null);
  const sig: Series = values.map(() => null);
  if (firstIdx >= 0) {
    const tail = ema(line.slice(firstIdx), signal);
    tail.forEach((v, j) => (sig[firstIdx + j] = v));
  }
  const hist: Series = line.map((v, i) => (v === null || sig[i] === null ? null : v - (sig[i] as number)));
  return { macd: line, signal: sig, hist };
}

/** Chart ranges, by calendar time so they mean the same on daily, weekly and monthly bars. */
export const CHART_RANGES = [
  { id: "1M", label: "1 month", months: 1 },
  { id: "3M", label: "3 months", months: 3 },
  { id: "6M", label: "6 months", months: 6 },
  { id: "YTD", label: "Year to date", months: 0 },
  { id: "1Y", label: "1 year", months: 12 },
  { id: "2Y", label: "2 years", months: 24 },
  { id: "3Y", label: "3 years", months: 36 },
  { id: "5Y", label: "5 years", months: 60 },
  { id: "ALL", label: "All", months: Infinity },
] as const;
export type ChartRange = (typeof CHART_RANGES)[number]["id"];

/** The first date the range shows, counted back from the last bar; null for All. */
export function rangeStart(last: string, range: ChartRange): string | null {
  const r = CHART_RANGES.find((x) => x.id === range);
  if (!r || !Number.isFinite(r.months)) return null;
  if (r.id === "YTD") return `${last.slice(0, 4)}-01-01`;
  const d = new Date(`${last}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() - r.months);
  return d.toISOString().slice(0, 10);
}

/** The RS line: the stock's close divided by the benchmark index's close on the same session,
 *  for each chart bar the value at its last session, scaled so its first value is 100. A
 *  session the index has no close for is skipped. */
export function rsLine(
  daily: OBar[],
  index: { day: string; close: number }[],
  bars: OBar[],
  interval: Interval,
): Series {
  const idx = new Map(index.map((c) => [c.day, c.close]));
  const period = periodOf(daily, interval);
  const byBar = new Map<string, number>();
  for (const b of daily) {
    const c = idx.get(b.day);
    const p = period.get(b.day);
    if (c && p) byBar.set(p, b.close / c);
  }
  const raw: Series = bars.map((b) => byBar.get(b.day) ?? null);
  const first = raw.find((v) => v !== null) ?? null;
  return raw.map((v) => (v === null || first === null ? null : (v / first) * 100));
}

/** Bars where the RS line closes at its highest of the last ``lookback`` bars (a new RS high,
 *  shown as a dot: leadership before or with the price). */
export function rsNewHighs(rs: Series, lookback: number): number[] {
  const out: number[] = [];
  for (let i = lookback; i < rs.length; i++) {
    const v = rs[i];
    if (v === null || v === undefined) continue;
    let high = -Infinity;
    for (let j = i - lookback; j < i; j++) {
      const w = rs[j];
      if (w !== null && w !== undefined && w > high) high = w;
    }
    if (v > high) out.push(i);
  }
  return out;
}

/** About a year of bars for each interval (the RS new-high lookback). */
export const YEAR_BARS: Record<Interval, number> = { D: 250, W: 52, M: 12 };
