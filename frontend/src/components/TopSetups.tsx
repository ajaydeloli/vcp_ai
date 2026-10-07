"use client";

import { useSetups } from "@/lib/api";
import { DASH, fmtInt, fmtNum, fmtPct, fmtPrice } from "@/lib/fmt";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";

/** The five best-ranked VCP setups; the full list is the Screener. */
export function TopSetups() {
  const vcp = useSetups("vcp");
  const rows = (vcp.data?.rows ?? []).slice(0, 5);
  return (
    <Card
      title="Top VCP setups"
      subtitle="Best score first; the whole list is in the Screener"
      right={
        <a href="/screener" className="text-xs text-accent hover:underline">
          Open the Screener →
        </a>
      }
      className="min-w-0"
    >
      {vcp.isError ? (
        <ErrorBox error={vcp.error} />
      ) : vcp.isPending ? (
        <Loading what="setups" />
      ) : rows.length === 0 ? (
        <Empty>No ranked VCP setup on this scan date.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-[11px] uppercase text-mute">
              <tr>
                <th className="px-2 py-1.5 font-medium">#</th>
                <th className="px-2 py-1.5 font-medium">Symbol</th>
                <th className="px-2 py-1.5 font-medium">Setup</th>
                <th className="px-2 py-1.5 text-right font-medium">Score</th>
                <th className="px-2 py-1.5 text-right font-medium">RS</th>
                <th className="px-2 py-1.5 text-right font-medium">Pivot</th>
                <th className="px-2 py-1.5 text-right font-medium">To pivot</th>
                <th className="px-2 py-1.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.instrument_id} className="border-t border-line">
                  <td className="px-2 py-1.5 text-mute">{i + 1}</td>
                  <td className="px-2 py-1.5">
                    <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium text-accent hover:underline">
                      {r.symbol}
                    </a>
                  </td>
                  <td className="px-2 py-1.5">
                    <GradeBadge classification={r.classification} grade={r.grade} />
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(r.score, 0)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{r.rs_rank === null ? DASH : fmtInt(r.rs_rank)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtPrice(r.pivot)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtPct(r.pivot_distance_pct, 1, true)}</td>
                  <td className="px-2 py-1.5">
                    <StatusPill status={r.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
