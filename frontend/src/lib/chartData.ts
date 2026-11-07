// Pure helpers that turn API data into chart series, marks and price lines. Kept free of the
// chart library so they can be tested without a canvas.
import { themeColor } from "./prefs";
import type { Bar, StockSetup } from "./schemas";

export const COLORS = {
  up: "#26c281",
  down: "#ef5350",
  upFade: "rgba(38,194,129,0.35)",
  downFade: "rgba(239,83,80,0.35)",
  sma20: "#f5c542",
  sma50: "#4aa3ff",
  sma200: "#c77dff",
  line: "#8ab4ff",
  pivot: "#26c281",
  stop: "#ef5350",
  // bright orange: the old blue (#2f6df6) disappeared against the dark chart background
  breakout: "#ff9f1c",
} as const;

export type ChartMarker = {
  time: string;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text: string;
  size?: number;
};

export type ChartLine = { price: number; title: string; color: string; dashed: boolean };

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
      color: themeColor("ink"),
      text: "Base start",
    });
  }
  for (const c of setup.contractions) {
    if (c.peak_date) {
      add({
        time: c.peak_date,
        position: "aboveBar",
        shape: "circle",
        color: themeColor("ink"),
        text: `P${c.sequence}`,
      });
    }
    if (c.trough_date) {
      const depth = c.depth_pct === null ? "" : ` ${c.depth_pct.toFixed(1)}%`;
      add({
        time: c.trough_date,
        position: "belowBar",
        shape: "circle",
        color: themeColor("ink"),
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
      color: themeColor("ink"),
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
      size: 2,
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
