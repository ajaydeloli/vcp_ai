// Past Leaders (owner, 2026-10-23): filters, export table and chart marks for the stored
// superperformance study. Pure helpers, free of the chart library, so they can be tested.
import { COLORS, type ChartMarker, type LinePoint } from "./chartData";
import type { Bar, LeaderBase, LeaderRow } from "./schemas";
import type { Cell } from "./tableFiles";

export type Band = "" | LeaderRow["gain_band"];
export type LeaderFilter = { q: string; year: string; sector: string; band: Band; vcp: boolean };
export const NO_FILTER: LeaderFilter = { q: "", year: "", sector: "", band: "", vcp: false };
export const UNCLASSIFIED = "Unclassified";

export const BAND_LABEL: Record<LeaderRow["gain_band"], string> = {
  "50-100": "+50 to +99 %",
  doubled: "Doubled (+100 to +199 %)",
  tripled: "Tripled or more (+200 %)",
};

export const stageLabel = (s: string | null): string | null =>
  s === null ? null : s === "INSUFFICIENT_DATA" ? "Not enough data" : s === "TRANSITION" ? "Transition" : s.replace("STAGE_", "Stage ");

/** The VCP class before the first breakout; null means no weekly scan covered the stock then. */
export const vcpLabel = (v: string | null): string => (v === null ? "No scan" : v === "VCP" ? "VCP" : v === "VCP_LIKE" ? "VCP-like" : "None");

export function filterLeaders(rows: LeaderRow[], f: LeaderFilter): LeaderRow[] {
  const q = f.q.trim().toUpperCase();
  return rows.filter(
    (r) =>
      (!q || r.symbol.includes(q)) &&
      (!f.year || r.launch_date.startsWith(f.year)) &&
      (!f.sector || (r.sector ?? UNCLASSIFIED) === f.sector) &&
      (!f.band || r.gain_band === f.band) &&
      (!f.vcp || r.vcp_before_breakout === "VCP"),
  );
}

const r2 = (v: number | null): number | null => (v === null ? null : Math.round(v * 100) / 100);

/** Every column of the runs for CSV / Excel, plain values (numbers stay numbers). */
export function leadersTable(rows: LeaderRow[]): { header: string[]; body: Cell[][] } {
  const header = [
    "Symbol", "Sector", "Launch date", "Launch price", "Peak date", "Peak price", "Gain %", "Gain band",
    "Sessions to peak", "Full year known", "Max pullback %", "Sessions to +50 %", "Stage at launch",
    "RS at launch", "Trend Template at launch", "% below 52-week high at launch", "First breakout",
    "Base sessions", "Base depth %", "VCP before breakout", "Further bases", "Give-back %",
    "Give-back of gain %",
  ]; // prettier-ignore
  const body = rows.map((r) => [
    r.symbol, r.sector ?? UNCLASSIFIED, r.launch_date, r2(r.launch_close), r.peak_date, r2(r.peak_close),
    r2(r.gain_pct), BAND_LABEL[r.gain_band], r.sessions_to_peak, r.complete ? "yes" : "no",
    r2(r.max_pullback_pct), r.to50, stageLabel(r.launch_stage), r.launch_rs,
    r.launch_tt === null ? null : r.launch_tt ? "pass" : "fail", r2(r.launch_dist_high_pct), r.breakout_date,
    r.base_length, r2(r.base_depth_pct), vcpLabel(r.vcp_before_breakout), r.bases_count, r2(r.giveback_pct),
    r2(r.giveback_of_gain_pct),
  ]); // prettier-ignore
  return { header, body };
}

/** Launch (below the bar), the top (above) and each breakout from a base (B1, B2 ...). A mark on a
 *  day with no bar is left out. */
export function leaderMarkers(run: LeaderRow, bases: LeaderBase[], bars: Bar[]): ChartMarker[] {
  const days = new Set(bars.map((b) => b.day));
  const out: ChartMarker[] = [];
  const add = (m: ChartMarker) => {
    if (days.has(m.time)) out.push(m);
  };
  add({ time: run.launch_date, position: "belowBar", shape: "arrowUp", color: COLORS.up, text: "Launch" });
  for (const b of bases) {
    add({ time: b.breakout_date, position: "aboveBar", shape: "circle", color: COLORS.breakout, text: `B${b.seq}` });
  }
  add({ time: run.peak_date, position: "aboveBar", shape: "arrowDown", color: COLORS.down, text: "Top" });
  return out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
}

/** One flat segment per base, from the base high (or the first bar shown) to its breakout. */
export function baseSegments(bases: LeaderBase[], bars: Bar[]): { seq: number; points: LinePoint[] }[] {
  if (bars.length === 0) return [];
  const first = bars[0]!.day;
  return bases
    .filter((b) => b.breakout_date >= first)
    .map((b) => {
      const from = b.base_start < first ? first : b.base_start;
      return {
        seq: b.seq,
        points: [
          { time: from, value: b.base_high },
          { time: b.breakout_date, value: b.base_high },
        ],
      };
    });
}
