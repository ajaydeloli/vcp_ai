// The Chart page's settings, kept per browser: interval, chart type, range, log scale, the RS
// benchmark, the moving averages (any number, each SMA or EMA with its own period and colour),
// the other indicators with their parameters, and the chart's colours. Storage can be blocked
// or empty: then the defaults are used and nothing breaks.
import { CHART_RANGES, type ChartRange, type Interval } from "./indicators";

export const CHART_KEY = "vcp-chart-settings";
export const LAST_SYMBOL_KEY = "vcp-chart-symbol";

export const CHART_TYPES = [
  { id: "candles", label: "Candlesticks" },
  { id: "hollow", label: "Hollow candles" },
  { id: "heikin", label: "Heikin Ashi" },
  { id: "ohlc", label: "OHLC bars" },
  { id: "line", label: "Line" },
  { id: "area", label: "Area" },
] as const;
export type ChartType = (typeof CHART_TYPES)[number]["id"];

/** Benchmarks for the RS line (NSE index names as stored). */
export const BENCHMARKS = ["Nifty 500", "Nifty 50", "Nifty Midcap 150", "Nifty Smallcap 250"] as const;
export type Benchmark = (typeof BENCHMARKS)[number];

export type MaKind = "SMA" | "EMA";
/** One moving average on the price. On weekly bars the period counts weeks. */
export type MovingAverage = { id: string; kind: MaKind; period: number; color: string; on: boolean };

/** The indicators other than the moving averages, with what can be set for each. */
export type Params = {
  volMa: { kind: MaKind; period: number };
  rsi: { period: number; upper: number; lower: number };
  macd: { fast: number; slow: number; signal: number };
  bb: { period: number; mult: number };
  /** the RS line's new-high lookback, in weeks (on daily bars five sessions a week) */
  rs: { weeks: number };
};

export const TOGGLES = [
  { id: "bb", label: "Bollinger bands", group: "On the price" },
  { id: "rs", label: "RS line", group: "Panes" },
  { id: "volume", label: "Volume", group: "Panes" },
  { id: "volma", label: "Volume average", group: "Panes" },
  { id: "rsi", label: "RSI", group: "Panes" },
  { id: "macd", label: "MACD", group: "Panes" },
  { id: "vcp", label: "VCP marks (base, contractions, breakout)", group: "Setup" },
  { id: "levels", label: "Pivot and stop lines", group: "Setup" },
] as const;
export type Toggle = (typeof TOGGLES)[number]["id"];

export const COLOR_KEYS = [
  { id: "up", label: "Candle up" },
  { id: "down", label: "Candle down" },
  { id: "wickUp", label: "Wick up" },
  { id: "wickDown", label: "Wick down" },
  { id: "line", label: "Line and area" },
  { id: "volUp", label: "Volume up" },
  { id: "volDown", label: "Volume down" },
  { id: "volma", label: "Volume average" },
  { id: "bb", label: "Bollinger bands" },
  { id: "rs", label: "RS line" },
  { id: "rsi", label: "RSI" },
  { id: "macd", label: "MACD line" },
  { id: "macdSignal", label: "MACD signal" },
  { id: "pivot", label: "Pivot line" },
  { id: "stop", label: "Stop line" },
] as const;
export type ColorKey = (typeof COLOR_KEYS)[number]["id"];
export type Colors = Record<ColorKey, string>;

export const DEFAULT_COLORS: Colors = {
  up: "#26c281",
  down: "#ef5350",
  wickUp: "#26c281",
  wickDown: "#ef5350",
  line: "#8ab4ff",
  volUp: "#26c281",
  volDown: "#ef5350",
  volma: "#ffd166",
  bb: "#8d99ae",
  rs: "#2f9bff",
  rsi: "#c77dff",
  macd: "#4aa3ff",
  macdSignal: "#ff9f1c",
  pivot: "#26c281",
  stop: "#ef5350",
};

/** The averages a new browser starts with (Minervini's 50, 150 and 200 days on). */
export const DEFAULT_AVERAGES: MovingAverage[] = [
  { id: "sma10", kind: "SMA", period: 10, color: "#f5c542", on: false },
  { id: "sma20", kind: "SMA", period: 20, color: "#ffd166", on: false },
  { id: "ema21", kind: "EMA", period: 21, color: "#f78c6b", on: false },
  { id: "sma50", kind: "SMA", period: 50, color: "#4aa3ff", on: true },
  { id: "sma150", kind: "SMA", period: 150, color: "#ff9f1c", on: true },
  { id: "sma200", kind: "SMA", period: 200, color: "#c77dff", on: true },
];

export const DEFAULT_PARAMS: Params = {
  volMa: { kind: "SMA", period: 50 },
  rsi: { period: 14, upper: 70, lower: 30 },
  macd: { fast: 12, slow: 26, signal: 9 },
  bb: { period: 20, mult: 2 },
  rs: { weeks: 52 },
};

export type ChartSettings = {
  interval: Interval;
  type: ChartType;
  range: ChartRange;
  log: boolean;
  benchmark: Benchmark;
  averages: MovingAverage[];
  /** the other indicators that are shown */
  toggles: Toggle[];
  params: Params;
  colors: Colors;
  /** the right sidebar's open tab (one at a time): auto = the VCP analysis when the stock has a
   *  setup, else the watch lists; none = folded into its tab buttons */
  sidePanel: SidePanel;
};

export type SidePanel = "auto" | "vcp" | "lists" | "none";

export const DEFAULT_CHART: ChartSettings = {
  interval: "D",
  type: "candles",
  range: "1Y",
  log: false,
  benchmark: "Nifty 500",
  averages: DEFAULT_AVERAGES,
  toggles: ["rs", "volume", "volma", "vcp", "levels"],
  params: DEFAULT_PARAMS,
  colors: DEFAULT_COLORS,
  sidePanel: "auto",
};

const HEX = /^#[0-9a-f]{6}$/i;
const isHex = (v: unknown): v is string => typeof v === "string" && HEX.test(v);
/** A whole number from ``lo`` to ``hi``, else ``fallback``. */
export const int = (v: unknown, lo: number, hi: number, fallback: number): number =>
  typeof v === "number" && Number.isInteger(v) && v >= lo && v <= hi ? v : fallback;
const num = (v: unknown, lo: number, hi: number, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi ? v : fallback;
const kind = (v: unknown, fallback: MaKind): MaKind => (v === "SMA" || v === "EMA" ? v : fallback);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" ? (v as Record<string, unknown>) : {});

export const MAX_AVERAGES = 12;
export const PERIOD_MAX = 500;

export function parseAverages(v: unknown, oldOn: Set<string> | null): MovingAverage[] {
  if (!Array.isArray(v)) {
    // settings saved before averages could be edited: the old fixed list, as it was switched on
    if (!oldOn) return DEFAULT_AVERAGES;
    return DEFAULT_AVERAGES.map((a) => ({ ...a, on: oldOn.has(a.id) }));
  }
  const seen = new Set<string>();
  const out: MovingAverage[] = [];
  for (const raw of v.slice(0, MAX_AVERAGES)) {
    const a = obj(raw);
    const id = typeof a.id === "string" && a.id && !seen.has(a.id) ? a.id : `ma${out.length}-${Math.random().toString(36).slice(2, 6)}`;
    seen.add(id);
    out.push({
      id,
      kind: kind(a.kind, "SMA"),
      period: int(a.period, 1, PERIOD_MAX, 50),
      color: isHex(a.color) ? a.color : "#4aa3ff",
      on: a.on !== false,
    });
  }
  return out;
}

export function parseParams(v: unknown): Params {
  const p = obj(v);
  const d = DEFAULT_PARAMS;
  const volMa = obj(p.volMa);
  const r = obj(p.rsi);
  const m = obj(p.macd);
  const b = obj(p.bb);
  const rs = obj(p.rs);
  const upper = int(r.upper, 51, 99, d.rsi.upper);
  const lower = int(r.lower, 1, 49, d.rsi.lower);
  const fast = int(m.fast, 1, 200, d.macd.fast);
  const slow = int(m.slow, 2, 300, d.macd.slow);
  return {
    volMa: { kind: kind(volMa.kind, d.volMa.kind), period: int(volMa.period, 1, PERIOD_MAX, d.volMa.period) },
    rsi: { period: int(r.period, 2, 100, d.rsi.period), upper, lower },
    macd: fast < slow ? { fast, slow, signal: int(m.signal, 1, 100, d.macd.signal) } : d.macd,
    bb: { period: int(b.period, 2, 200, d.bb.period), mult: num(b.mult, 0.5, 5, d.bb.mult) },
    rs: { weeks: int(rs.weeks, 4, 260, d.rs.weeks) },
  };
}

export function parseColors(v: unknown): Colors {
  const c = obj(v);
  const out = { ...DEFAULT_COLORS };
  for (const { id } of COLOR_KEYS) if (isHex(c[id])) out[id] = c[id] as string;
  return out;
}

export function parseChart(raw: string | null): ChartSettings {
  try {
    const v = raw ? obj(JSON.parse(raw)) : {};
    const toggleIds = new Set<string>(TOGGLES.map((t) => t.id));
    // ``indicators`` is the older format: averages and the other indicators in one list
    const old = Array.isArray(v.indicators) ? new Set(v.indicators.filter((x): x is string => typeof x === "string")) : null;
    const toggles = Array.isArray(v.toggles)
      ? (v.toggles.filter((x) => typeof x === "string" && toggleIds.has(x)) as Toggle[])
      : old
        ? ([...old].filter((x) => toggleIds.has(x)) as Toggle[])
        : DEFAULT_CHART.toggles;
    return {
      interval: v.interval === "W" || v.interval === "M" ? v.interval : "D",
      type: CHART_TYPES.some((t) => t.id === v.type) ? (v.type as ChartType) : DEFAULT_CHART.type,
      range: CHART_RANGES.some((r) => r.id === v.range) ? (v.range as ChartRange) : DEFAULT_CHART.range,
      log: v.log === true,
      benchmark: BENCHMARKS.includes(v.benchmark as Benchmark) ? (v.benchmark as Benchmark) : DEFAULT_CHART.benchmark,
      averages: parseAverages(v.averages, old),
      toggles: [...new Set(toggles)],
      params: parseParams(v.params),
      colors: parseColors(v.colors),
      sidePanel: v.sidePanel === "vcp" || v.sidePanel === "lists" || v.sidePanel === "none" ? v.sidePanel : "auto",
    };
  } catch {
    return DEFAULT_CHART;
  }
}

/** A colour that fits next to the averages already on the chart, for a new one. */
const NEW_COLORS = ["#06d6a0", "#ef476f", "#118ab2", "#b388eb", "#f78c6b", "#ffd166", "#4aa3ff", "#ff9f1c"];
export function newAverage(existing: MovingAverage[]): MovingAverage {
  const used = new Set(existing.map((a) => a.color.toLowerCase()));
  const color = NEW_COLORS.find((c) => !used.has(c)) ?? NEW_COLORS[existing.length % NEW_COLORS.length]!;
  return { id: `ma-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`, kind: "SMA", period: 30, color, on: true };
}

export const maLabel = (a: Pick<MovingAverage, "kind" | "period">) => `${a.kind} ${a.period}`;

/** How many indicators are shown (the toolbar's count). */
export const shownCount = (s: ChartSettings) => s.averages.filter((a) => a.on).length + s.toggles.length;

/** ``#rrggbb`` with an alpha, for the volume bars. */
export function fade(hex: string, alpha: number): string {
  const n = Number.parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

export function loadChart(): ChartSettings {
  try {
    return parseChart(window.localStorage.getItem(CHART_KEY));
  } catch {
    return DEFAULT_CHART;
  }
}

export function saveChart(s: ChartSettings): void {
  try {
    window.localStorage.setItem(CHART_KEY, JSON.stringify(s));
  } catch {
    /* storage blocked: the settings last until the page is reloaded */
  }
}

export function lastSymbol(): string | null {
  try {
    return window.localStorage.getItem(LAST_SYMBOL_KEY);
  } catch {
    return null;
  }
}

export function rememberSymbol(symbol: string): void {
  try {
    window.localStorage.setItem(LAST_SYMBOL_KEY, symbol);
  } catch {
    /* ignore */
  }
}
