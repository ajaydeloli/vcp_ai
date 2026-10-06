// Pure helpers that turn API data into chart series, marks and price lines. Kept free of the
// chart library so they can be tested without a canvas.
import type { Bar, StockSetup } from "./schemas";

export const COLORS = {
  up: "#26c281",
  down: "#ef5350",
  upFade: "rgba(38,194,129,0.35)",
  downFade: "rgba(239,83,80,0.35)",
  sma20: "#f5c542",
  sma50: "#4aa3ff",
  sma200: "#c77dff",
  pivot: "#26c281",
  stop: "#ef5350",
  mark: "#e6ecf8",
  breakout: "#2f6df6",
} as const;

export type Candle = { time: string; open: number; high: number; low: number; close: number };
export type VolumePoint = { time: string; value: number; color: string };
export type LinePoint = { time: string; value: number };

export type ChartMarker = {
  time: string;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text: string;
};

export type ChartLine = { price: number; title: string; color: string; dashed: boolean };

export const RANGES = [
  { id: "3M", label: "3M", bars: 63 },
  { id: "6M", label: "6M", bars: 126 },
  { id: "1Y", label: "1Y", bars: 252 },
  { id: "ALL", label: "All", bars: Infinity },
] as const;
export type RangeId = (typeof RANGES)[number]["id"];

export function toCandles(bars: Bar[]): Candle[] {
  const out: Candle[] = [];
  for (const b of bars) {
    if (b.open === null || b.high === null || b.low === null || b.close === null) continue;
    out.push({ time: b.day, open: b.open, high: b.high, low: b.low, close: b.close });
  }
  return out;
}

export function toVolume(bars: Bar[]): VolumePoint[] {
  const out: VolumePoint[] = [];
  for (const b of bars) {
    if (b.volume === null || b.open === null || b.close === null) continue;
    out.push({
      time: b.day,
      value: b.volume,
      color: b.close >= b.open ? COLORS.upFade : COLORS.downFade,
    });
  }
  return out;
}

/** A moving average as a line; days without a value are left out (never drawn as 0). */
export function toSma(bars: Bar[], key: "sma20" | "sma50" | "sma200"): LinePoint[] {
  const out: LinePoint[] = [];
  for (const b of bars) {
    const v = b[key];
    if (v !== null) out.push({ time: b.day, value: v });
  }
  return out;
}

/** The first day to show for a range button (the last N bars); null shows everything. */
export function rangeFrom(bars: Bar[], range: RangeId): string | null {
  const n = RANGES.find((r) => r.id === range)?.bars ?? Infinity;
  if (!Number.isFinite(n) || bars.length <= n) return null;
  return bars[bars.length - n]?.day ?? null;
}

/** Marks for the selected strategy's setup: base start, VCP contractions or the detector's
 *  points, and the breakout. Marks on days with no bar are dropped (they could not be placed). */
export function buildMarkers(bars: Bar[], setup: StockSetup | null): ChartMarker[] {
  if (!setup) return [];
  const byDay = new Map(bars.map((b) => [b.day, b]));
  const out: ChartMarker[] = [];
  const add = (m: ChartMarker) => {
    if (byDay.has(m.time)) out.push(m);
  };
  if (setup.base_start) {
    add({
      time: setup.base_start,
      position: "belowBar",
      shape: "square",
      color: COLORS.mark,
      text: "Base start",
    });
  }
  for (const c of setup.contractions) {
    if (c.peak_date) {
      add({
        time: c.peak_date,
        position: "aboveBar",
        shape: "circle",
        color: COLORS.mark,
        text: `P${c.sequence}`,
      });
    }
    if (c.trough_date) {
      const depth = c.depth_pct === null ? "" : ` ${c.depth_pct.toFixed(1)}%`;
      add({
        time: c.trough_date,
        position: "belowBar",
        shape: "circle",
        color: COLORS.mark,
        text: `T${c.sequence}${depth}`,
      });
    }
  }
  for (const p of setup.points) {
    const bar = byDay.get(p.day);
    const high = p.price !== null && bar?.high === p.price;
    add({
      time: p.day,
      position: high ? "aboveBar" : "belowBar",
      shape: "circle",
      color: COLORS.mark,
      text: p.label,
    });
  }
  if (setup.breakout_date) {
    add({
      time: setup.breakout_date,
      position: "belowBar",
      shape: "arrowUp",
      color: COLORS.breakout,
      text: "Breakout",
    });
  }
  // the chart wants marks in time order; two marks on a day keep their order
  return out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
}

export function buildPriceLines(setup: StockSetup | null): ChartLine[] {
  if (!setup) return [];
  const lines: ChartLine[] = [];
  if (setup.pivot !== null) {
    lines.push({ price: setup.pivot, title: "Pivot", color: COLORS.pivot, dashed: false });
  }
  if (setup.stop !== null) {
    lines.push({ price: setup.stop, title: "Stop", color: COLORS.stop, dashed: true });
  }
  return lines;
}
