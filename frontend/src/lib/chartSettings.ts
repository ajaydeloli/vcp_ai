// The Chart page's settings (interval, chart type, range, indicators), kept per browser. Storage
// can be blocked or empty: then the defaults are used and nothing breaks.
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

export type IndicatorGroup = "Moving averages" | "Bands" | "Panes" | "Setup";
export type IndicatorDef = { id: string; label: string; group: IndicatorGroup; color: string };

/** Everything the Indicators menu offers. On weekly bars an average counts weeks (SMA 10 and
 *  SMA 30 weekly are Minervini's 50 and 150 days). */
export const INDICATORS: IndicatorDef[] = [
  { id: "sma10", label: "SMA 10", group: "Moving averages", color: "#f5c542" },
  { id: "sma20", label: "SMA 20", group: "Moving averages", color: "#ffd166" },
  { id: "sma30", label: "SMA 30", group: "Moving averages", color: "#06d6a0" },
  { id: "sma50", label: "SMA 50", group: "Moving averages", color: "#4aa3ff" },
  { id: "sma150", label: "SMA 150", group: "Moving averages", color: "#ff9f1c" },
  { id: "sma200", label: "SMA 200", group: "Moving averages", color: "#c77dff" },
  { id: "ema10", label: "EMA 10", group: "Moving averages", color: "#ef476f" },
  { id: "ema21", label: "EMA 21", group: "Moving averages", color: "#f78c6b" },
  { id: "ema50", label: "EMA 50", group: "Moving averages", color: "#118ab2" },
  { id: "ema200", label: "EMA 200", group: "Moving averages", color: "#b388eb" },
  { id: "bb", label: "Bollinger bands (20, 2)", group: "Bands", color: "#8d99ae" },
  { id: "volume", label: "Volume", group: "Panes", color: "#8ab4ff" },
  { id: "volma", label: "Volume average (50)", group: "Panes", color: "#ffd166" },
  { id: "rsi", label: "RSI (14)", group: "Panes", color: "#c77dff" },
  { id: "macd", label: "MACD (12, 26, 9)", group: "Panes", color: "#4aa3ff" },
  { id: "vcp", label: "VCP marks (base, contractions, breakout)", group: "Setup", color: "#ff9f1c" },
  { id: "levels", label: "Pivot and stop lines", group: "Setup", color: "#26c281" },
];

export type ChartSettings = {
  interval: Interval;
  type: ChartType;
  range: ChartRange;
  indicators: string[];
  log: boolean;
};

export const DEFAULT_CHART: ChartSettings = {
  interval: "D",
  type: "candles",
  range: "1Y",
  indicators: ["sma50", "sma150", "sma200", "volume", "volma", "vcp", "levels"],
  log: false,
};

export function parseChart(raw: string | null): ChartSettings {
  try {
    const v = raw ? (JSON.parse(raw) as Partial<ChartSettings>) : {};
    const ids = new Set(INDICATORS.map((i) => i.id));
    return {
      interval: v.interval === "W" || v.interval === "M" ? v.interval : "D",
      type: CHART_TYPES.some((t) => t.id === v.type) ? (v.type as ChartType) : DEFAULT_CHART.type,
      range: CHART_RANGES.some((r) => r.id === v.range) ? (v.range as ChartRange) : DEFAULT_CHART.range,
      indicators: Array.isArray(v.indicators) ? v.indicators.filter((x) => ids.has(x)) : DEFAULT_CHART.indicators,
      log: v.log === true,
    };
  } catch {
    return DEFAULT_CHART;
  }
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
