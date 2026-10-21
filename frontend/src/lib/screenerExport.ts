// Screener export and the symbol check for watch-list import (owner decision 2026-10-21). Both read
// the local API only, page by page (at most 100 rows a page).
import { useQuery } from "@tanstack/react-query";
import type { z } from "zod";
import { getJson, paths } from "./api";
import { DEFAULT_QUERY, type ScreenerQuery } from "./screener";
import * as s from "./schemas";
import type { Cell } from "./tableFiles";

type Row = z.infer<typeof s.screenerRow>;
const PAGE = 100;
const MAX_PAGES = 60;

/** Every row matching the filters, in the page's sort order. */
export async function allScreenerRows(q: ScreenerQuery): Promise<{ rows: Row[]; asOf: string | null }> {
  const rows: Row[] = [];
  let asOf: string | null = null;
  for (let page = 1; page <= MAX_PAGES; page++) {
    const r = await getJson(paths.screener({ ...q, page, pageSize: PAGE }), s.screener);
    asOf = r.as_of ?? asOf;
    rows.push(...r.rows);
    if (r.rows.length < PAGE || rows.length >= r.total) break;
  }
  return { rows, asOf };
}

const yes = (b: boolean | null | undefined) => (b === null || b === undefined ? null : b ? "Yes" : "No");
const round = (x: number | null | undefined, d: number) => (x === null || x === undefined ? null : Number(x.toFixed(d)));

/** The export columns for the table's visible column keys (rank, star and live price left out). */
const EXPORT: Record<string, [string, (r: Row) => Cell][]> = {
  symbol: [
    ["Symbol", (r) => r.symbol],
    ["Company", (r) => r.company],
  ],
  universe: [
    ["In universe", (r) => yes(r.in_universe)],
    ["Outside reason", (r) => r.outside_reason],
  ],
  sector: [["Sector", (r) => r.sector ?? null]],
  stage: [["Stage", (r) => r.stage]],
  conditions_passed: [
    ["Trend Template", (r) => (r.trend_template_pass === null ? null : r.trend_template_pass ? "Pass" : "Fail")],
    ["Conditions passed", (r) => r.conditions_passed],
    ["Conditions total", (r) => r.conditions_total],
  ],
  near_high: [["Near 52w high", (r) => yes(r.near_52w_high)]],
  rs_rank: [["RS rank", (r) => r.rs_rank]],
  close: [["Close", (r) => round(r.close, 2)]],
  change_pct: [["Change %", (r) => round(r.change_pct, 2)]],
  grade: [
    ["Classification", (r) => r.classification],
    ["Grade", (r) => r.grade],
  ],
  score: [["Score", (r) => round(r.score, 1)]],
  pivot: [["Pivot", (r) => round(r.pivot, 2)]],
  status: [["Status", (r) => r.status]],
  pivot_distance_pct: [["To pivot %", (r) => round(r.pivot_distance_pct, 2)]],
  eps_yoy: [["EPS YoY %", (r) => round(r.eps_yoy, 1)]],
  sales_yoy: [["Sales YoY %", (r) => round(r.sales_yoy, 1)]],
};

export function screenerTable(rows: Row[], visibleKeys: string[]): { header: string[]; body: Cell[][] } {
  const cols = visibleKeys.flatMap((k) => EXPORT[k] ?? []);
  return { header: cols.map(([h]) => h), body: rows.map((r) => cols.map(([, get]) => get(r))) };
}

/** Which of these symbols exist (any NSE stock we have, inside the scan universe or not). */
export async function knownSymbols(candidates: string[]): Promise<Set<string>> {
  const known = new Set<string>();
  const chunks = symbolBatches([...new Set(candidates.filter(Boolean))]);
  for (const symbols of chunks) {
    const { rows } = await allScreenerRows({ ...DEFAULT_QUERY, symbols, universeOnly: false, eqOnly: false });
    for (const r of rows) known.add(r.symbol);
  }
  return known;
}

/** Split symbols into batches that fit the API's 2000-character `symbols` parameter. */
export function symbolBatches(symbols: string[], maxChars = 1800): string[][] {
  const out: string[][] = [];
  let cur: string[] = [];
  let len = 0;
  for (const x of symbols) {
    if (len + x.length + 1 > maxChars && cur.length) {
      out.push(cur);
      cur = [];
      len = 0;
    }
    cur.push(x);
    len += x.length + 1;
  }
  if (cur.length) out.push(cur);
  return out;
}

/** The screener rows of a symbol list of any length: batch by batch, then sorted together. */
export async function rowsForSymbols(q: ScreenerQuery, symbols: string[]): Promise<{ rows: Row[]; asOf: string | null }> {
  const batches = symbolBatches(symbols);
  let asOf: string | null = null;
  const rows: Row[] = [];
  for (const b of batches) {
    const r = await allScreenerRows({ ...q, symbols: b });
    asOf = r.asOf ?? asOf;
    rows.push(...r.rows);
  }
  if (batches.length > 1) {
    const key = q.sort as keyof Row;
    const sign = q.direction === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      const x = a[key];
      const y = b[key];
      if (x === null || x === undefined) return y === null || y === undefined ? 0 : 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sign;
    });
  }
  return { rows, asOf };
}

/** A watch list's rows (any length), refreshed like the other screener queries. */
export const useSymbolRows = (q: ScreenerQuery, symbols: string[]) =>
  useQuery({
    queryKey: ["symbolRows", q, symbols],
    queryFn: async () => {
      const r = await rowsForSymbols(q, symbols);
      return { rows: r.rows, as_of: r.asOf };
    },
    placeholderData: (prev) => prev,
    enabled: symbols.length > 0,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });
