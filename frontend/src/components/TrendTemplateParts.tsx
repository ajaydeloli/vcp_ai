"use client";

// Pieces of the Trend Template page: plain-language condition names, the funnel, the stage bars.
import { fmtInt, fmtPrice } from "@/lib/fmt";
import type { TrendTemplateData, TTStock } from "@/lib/schemas";

/** The ten conditions of TREND_TEMPLATE_SPECIFICATION section 2, in plain words. */
export const CONDITIONS: Record<string, { label: string; rule: string }> = {
  close_above_sma150: { label: "Price above 150-day avg", rule: "Close above its 150-day moving average" },
  close_above_sma200: { label: "Price above 200-day avg", rule: "Close above its 200-day moving average" },
  sma150_above_sma200: { label: "150-day avg above 200-day", rule: "The 150-day average is above the 200-day average" },
  sma200_rising: { label: "200-day avg rising", rule: "The 200-day average is higher than 21 sessions (about a month) ago" },
  sma50_above_sma150: { label: "50-day avg above 150-day", rule: "The 50-day average is above the 150-day average" },
  sma50_above_sma200: { label: "50-day avg above 200-day", rule: "The 50-day average is above the 200-day average" },
  close_above_sma50: { label: "Price above 50-day avg", rule: "Close above its 50-day moving average" },
  above_52w_low: { label: "25%+ above 52-week low", rule: "Close at least 25% above the lowest low of the last 252 sessions" },
  near_52w_high: { label: "Within 25% of 52-week high", rule: "Close no more than 25% below the highest high of the last 252 sessions" },
  rs_rank_min: { label: "RS rank 70 or more", rule: "Relative Strength rank of at least 70 (1 to 99 against our stocks)" },
};
export const conditionLabel = (name: string) => CONDITIONS[name]?.label ?? name;

export const STAGE_ORDER = ["STAGE_1", "STAGE_2", "STAGE_3", "STAGE_4", "TRANSITION"];
export const STAGE_NAME: Record<string, string> = { STAGE_1: "Stage 1", STAGE_2: "Stage 2", STAGE_3: "Stage 3", STAGE_4: "Stage 4", TRANSITION: "Transition" };
export const STAGE_BG: Record<string, string> = { STAGE_1: "bg-mute/50", STAGE_2: "bg-up", STAGE_3: "bg-warn", STAGE_4: "bg-down", TRANSITION: "bg-accent/60" };
export const stageName = (s: string | null | undefined) => (s ? (STAGE_NAME[s] ?? s) : "–");

/** How far a failed condition is from passing, in words: "489 vs 517 needed (5.5% short)". */
export function gap(f: TTStock["failed"][number]): string {
  if (f.measurement === null || f.threshold === null) return "";
  const short = f.threshold !== 0 ? Math.abs((f.threshold - f.measurement) / f.threshold) * 100 : null;
  const v = (x: number) => (f.name === "rs_rank_min" ? fmtInt(x) : fmtPrice(x));
  return `${v(f.measurement)} vs ${v(f.threshold)} needed${short !== null ? ` (${short.toFixed(1)}% short)` : ""}`;
}

export function Funnel({ d }: { d: TrendTemplateData }) {
  const pct = (n: number) => (d.population ? (100 * n) / d.population : 0);
  const rows = [
    ...d.conditions.map((c) => ({ key: c.name, label: conditionLabel(c.name), rule: CONDITIONS[c.name]?.rule ?? c.name, n: c.passed, only: c.only_fail })),
    { key: "all", label: "All 10 conditions", rule: "Passes the Trend Template", n: d.passing, only: null },
  ];
  return (
    <ul className="space-y-1.5" aria-label="Stocks passing each condition">
      {rows.map((r) => (
        <li key={r.key} className="grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)_auto] items-center gap-2 text-xs" title={r.rule}>
          <span className={r.key === "all" ? "font-semibold text-ink" : "text-mute"}>{r.label}</span>
          <span className="h-3 overflow-hidden rounded bg-line/60">
            <span className={`block h-full rounded ${r.key === "all" ? "bg-up" : "bg-accent/70"}`} style={{ width: `${pct(r.n)}%` }} />
          </span>
          <span className="w-36 text-right tabular-nums text-ink">
            {fmtInt(r.n)} <span className="text-mute">({pct(r.n).toFixed(0)}%)</span>
            {r.only ? (
              <span className="ml-1 text-warn" title={`${r.only} stocks fail only this condition`}>
                · {r.only} only
              </span>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function StageBars({ d }: { d: TrendTemplateData }) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-3 text-[11px] text-mute" aria-hidden="true">
        {STAGE_ORDER.map((s) => (
          <span key={s} className="inline-flex items-center gap-1">
            <span className={`inline-block h-2 w-3 rounded-sm ${STAGE_BG[s]}`} />
            {STAGE_NAME[s]}
          </span>
        ))}
      </div>
      <table className="w-full table-fixed text-xs">
        <caption className="sr-only">Weekly stage of our stocks by sector</caption>
        <thead className="text-mute">
          <tr>
            <th className="w-48 py-1 text-left font-normal">Sector</th>
            <th className="py-1 text-left font-normal">Stages</th>
            <th className="w-20 py-1 text-right font-normal">Stage 2</th>
            <th className="w-14 py-1 text-right font-normal">Stocks</th>
          </tr>
        </thead>
        <tbody>
          {d.stages.map((r) => {
            const s2 = r.counts.STAGE_2 ?? 0;
            return (
              <tr key={r.sector} className={`border-t border-line ${r.sector === "All" ? "font-semibold" : ""}`}>
                <td className="truncate py-1 text-ink">{r.sector === "All" ? "All our stocks" : r.sector}</td>
                <td className="py-1">
                  <span className="flex h-3 overflow-hidden rounded" title={STAGE_ORDER.map((s) => `${STAGE_NAME[s]}: ${r.counts[s] ?? 0}`).join(", ")}>
                    {STAGE_ORDER.map((s) => (r.counts[s] ? <span key={s} className={STAGE_BG[s]} style={{ width: `${(100 * (r.counts[s] ?? 0)) / r.total}%` }} /> : null))}
                  </span>
                </td>
                <td className="py-1 text-right tabular-nums text-ink">{r.total ? `${((100 * s2) / r.total).toFixed(0)}%` : "–"}</td>
                <td className="py-1 text-right tabular-nums text-mute">{fmtInt(r.total)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
