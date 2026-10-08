"use client";

import { useSetups } from "@/lib/api";
import { DASH, fmtInt } from "@/lib/fmt";
import { countList } from "@/lib/lists";
import { Empty, ErrorBox, Loading } from "./ui";

const ROWS: { id: string; label: string; color: string }[] = [
  { id: "aplus", label: "A+ VCP setups", color: "bg-up" },
  { id: "vcp", label: "VCP setups", color: "bg-accent" },
  { id: "forming", label: "Forming bases", color: "bg-violet" },
  { id: "watch", label: "Breakout watch", color: "bg-warn" },
];

/** How many stocks are in each list of the VCP ranking, laid out like the market stage card. */
export function SetupCounts({ box = "border border-line bg-panel2" }: { box?: string }) {
  const vcp = useSetups("vcp");
  const rows = vcp.data?.rows ?? null;
  const counts = rows ? ROWS.map((r) => countList(rows, r.id)) : null;
  const max = counts ? Math.max(...counts, 1) : 1;
  const leader = counts ? ROWS[counts.indexOf(Math.max(...counts))]! : null;
  return (
    <section className={`min-w-0 rounded-lg p-4 ${box}`} aria-label="Setups today">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-mute">Setups today</h3>
      <p className="mb-3 mt-0.5 text-xs text-mute">The lists of the VCP ranking</p>
      {vcp.isError ? (
        <ErrorBox error={vcp.error} />
      ) : !rows || !counts ? (
        <Loading what="setups" />
      ) : counts.every((n) => n === 0) ? (
        <Empty>No setups in the latest scan.</Empty>
      ) : (
        <>
          <p className="mb-3 text-lg font-semibold text-ink">Most setups are {leader?.label}</p>
          <ul className="space-y-2.5 text-xs">
            {ROWS.map((r, i) => (
              <li key={r.id} className="grid grid-cols-[7.5rem_1fr_5rem] items-center gap-2">
                <span className="text-ink">{r.label}</span>
                <span className="h-2 overflow-hidden rounded bg-bg">
                  <span className={`block h-full ${r.color}`} style={{ width: `${((counts[i] ?? 0) / max) * 100}%` }} />
                </span>
                <span className="text-right tabular-nums text-mute" data-testid={`kpi-${r.label}`}>
                  {rows ? fmtInt(counts[i]) : DASH}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-mute">{countList(rows, "vcp_like")} more stocks are VCP like.</p>
        </>
      )}
    </section>
  );
}
