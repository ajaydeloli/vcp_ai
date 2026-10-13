"use client";

// New highs today (FRONTEND_SPECIFICATION §67.36): the universe stocks at a new 52-week high on
// the market's last day, one row each, best RS first. Display only. Only the rows scroll.
import { fmtNum, fmtPct, tone } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { Inner } from "./MarketParts";
import { Empty } from "./ui";

type High = Health["new_highs"][number];

// stored as STAGE_1..STAGE_4 or TRANSITION
const stageNo = (s: string | null | undefined) => (s ? s.replace(/^STAGE_/, "") : null);
const stageText = (s: string | null | undefined) => {
  const n = stageNo(s);
  return n === null ? "—" : n === "TRANSITION" ? "Trans." : `Stage ${n}`;
};
const stageClass = (s: string | null | undefined) => {
  const n = stageNo(s);
  return n === "2" ? "text-up" : n === "4" ? "text-down" : n === "3" ? "text-warn" : "text-mute";
};

const HEAD: [string, string, string][] = [
  ["Symbol", "text-left", "Opens the stock page"],
  ["Sector", "text-left", "NSE sector"],
  ["Close", "", "Adjusted close today"],
  ["Day", "", "Change today"],
  ["Volume", "", "Volume today vs the average of the 50 sessions before"],
  ["vs 50-day", "", "Close vs its 50-day average: how stretched the move is"],
  ["RS", "", "Relative-strength rank (1-99) on the latest Trend Template scan"],
  ["TT", "", "Passes the Trend Template on the latest scan"],
  ["Stage", "", "Weekly stage on the latest scan"],
];

export function NewHighsCard({ highs, box }: { highs: High[]; box: string }) {
  const tt = highs.filter((h) => h.trend_template).length;
  return (
    <Inner title="New highs today" box={box} fill>
      {highs.length === 0 ? (
        <Empty>No stock of the universe made a new 52-week high today.</Empty>
      ) : (
        <>
          <p className="mb-2 shrink-0 text-[11px] text-mute" data-testid="new-highs-summary">
            <span className="font-medium text-ink">{highs.length}</span> stocks at a new 52-week high today; <span className="font-medium text-ink">{tt}</span> pass the
            Trend Template. Best RS first.
          </p>
          <div className="min-h-0 max-h-72 flex-1 overflow-auto lg:max-h-none" data-testid="new-highs-scroll">
            <table className="w-full min-w-[540px] text-right text-xs tabular-nums">
              <thead className="sticky top-0 z-10 bg-panel2 text-[10px] uppercase text-mute">
                <tr>
                  {HEAD.map(([h, align, title]) => (
                    <th key={h} scope="col" className={`px-1 py-1 font-medium first:pl-0 ${align}`} title={title}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {highs.map((h) => (
                  <tr key={h.symbol} className="border-t border-line/60">
                    <th scope="row" className="py-1 pr-1 text-left font-medium">
                      <a href={`/stocks/${encodeURIComponent(h.symbol)}`} className="text-ink hover:text-accent">
                        {h.symbol}
                      </a>
                    </th>
                    <td className={`max-w-[8.5rem] truncate px-1 py-1 text-left ${h.sector ? "text-mute" : "text-mute/60"}`} title={h.sector ?? "Not classified yet"}>
                      {h.sector ?? "—"}
                    </td>
                    <td className="px-1 py-1 text-ink">{fmtNum(h.close ?? null, 2)}</td>
                    <td className={`px-1 py-1 ${tone(h.change_pct ?? null)}`}>{fmtPct(h.change_pct ?? null, 1, true)}</td>
                    <td className={`px-1 py-1 ${(h.volume_ratio ?? 0) >= 1.5 ? "text-up" : "text-ink"}`}>{h.volume_ratio === null || h.volume_ratio === undefined ? "—" : `${h.volume_ratio.toFixed(1)}×`}</td>
                    <td className={`px-1 py-1 ${(h.vs_sma50_pct ?? 0) > 25 ? "text-warn" : "text-ink"}`}>{fmtPct(h.vs_sma50_pct ?? null, 1, true)}</td>
                    <td className={`px-1 py-1 ${(h.rs_rank ?? 0) >= 80 ? "text-up" : "text-ink"}`}>{h.rs_rank ?? "—"}</td>
                    <td className={`px-1 py-1 ${h.trend_template ? "text-up" : "text-mute"}`}>{h.trend_template === null || h.trend_template === undefined ? "—" : h.trend_template ? "✓" : "✗"}</td>
                    <td className={`px-1 py-1 ${stageClass(h.stage)}`}>{stageText(h.stage)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Inner>
  );
}
