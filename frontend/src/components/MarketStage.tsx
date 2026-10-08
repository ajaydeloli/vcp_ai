"use client";

import { useScreener } from "@/lib/api";
import { fmtInt, fmtPct } from "@/lib/fmt";
import { DEFAULT_QUERY } from "@/lib/screener";
import { Empty, ErrorBox, Loading } from "./ui";

const ROWS: { id: string; label: string; color: string }[] = [
  { id: "STAGE_1", label: "Stage 1: base", color: "bg-accent" },
  { id: "STAGE_2", label: "Stage 2: uptrend", color: "bg-up" },
  { id: "STAGE_3", label: "Stage 3: top", color: "bg-warn" },
  { id: "STAGE_4", label: "Stage 4: downtrend", color: "bg-down" },
  { id: "TRANSITION", label: "Transition", color: "bg-violet" },
];

/** Where the scanned stocks stand in the weekly stage cycle (counts from the Trend Template scan). */
export function MarketStage() {
  const result = useScreener({ ...DEFAULT_QUERY, pageSize: 1 });
  const d = result.data;
  const counts = d?.stage_counts ?? {};
  const total = d?.scanned ?? 0;
  const leader = ROWS.reduce((a, r) => ((counts[r.id] ?? 0) > (counts[a.id] ?? 0) ? r : a), ROWS[0]!);
  return (
    <section className="min-w-0 rounded-lg border border-line bg-panel2 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-mute">Market stage</h3>
      <p className="mb-3 mt-0.5 text-xs text-mute">
        {d ? `${fmtInt(total)} scanned stocks by weekly stage` : "Weekly stage of the scanned stocks"}
      </p>
      {result.isError ? (
        <ErrorBox error={result.error} />
      ) : !d ? (
        <Loading what="market stage" />
      ) : total === 0 ? (
        <Empty>No scan to show.</Empty>
      ) : (
        <>
          <p className="mb-3 text-lg font-semibold text-ink">
            Most stocks are in {leader.label.split(":")[0]}
          </p>
          <ul className="space-y-2.5 text-xs">
            {ROWS.map((r) => {
              const n = counts[r.id] ?? 0;
              const pct = total ? (n / total) * 100 : null;
              return (
                <li key={r.id} className="grid grid-cols-[7.5rem_1fr_5rem] items-center gap-2">
                  <span className="text-ink">{r.label}</span>
                  <span className="h-2 overflow-hidden rounded bg-panel2">
                    <span className={`block h-full ${r.color}`} style={{ width: `${pct ?? 0}%` }} />
                  </span>
                  <span className="text-right tabular-nums text-mute">
                    {fmtInt(n)} · {fmtPct(pct, 0)}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
