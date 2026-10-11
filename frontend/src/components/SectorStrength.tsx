"use client";

// Sector strength (FRONTEND_SPECIFICATION §67.28, step 5): today's universe by NSE sector.
// Display only. A row per sector, strongest first; the stocks not classified yet are one row.
import { fmtInt, fmtNum, fmtPct, tone } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { Inner } from "./MarketParts";
import { Empty } from "./ui";

const MONTH = 21; // sessions (api/health.py SECTOR_MONTH)

const pp = (v: number | null) => (v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(0)} pp`);

export function SectorCard({ sectors, box }: { sectors: Health["sectors"]; box: string }) {
  const classified = sectors.filter((s) => s.sector !== null);
  const rest = sectors.find((s) => s.sector === null);
  return (
    <Inner title="Sector strength" box={box}>
      {sectors.length === 0 ? (
        <Empty>No sector data yet.</Empty>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-right text-xs tabular-nums">
              <thead className="text-[10px] uppercase text-mute">
                <tr>
                  <th scope="col" className="py-1 pr-2 text-left font-medium">Sector</th>
                  <th scope="col" className="px-1 py-1 font-medium">Stocks</th>
                  <th scope="col" className="px-1 py-1 text-left font-medium" title="Share closing above its 50-day average today">
                    Above 50-day
                  </th>
                  <th scope="col" className="px-1 py-1 font-medium" title={`Change of that share over ${MONTH} sessions (same stocks)`}>
                    1-month
                  </th>
                  <th scope="col" className="px-1 py-1 font-medium">Median RS</th>
                  <th scope="col" className="px-1 py-1 font-medium" title={`Stocks with a breakout (any strategy) in the last ${MONTH} sessions`}>
                    Breakouts
                  </th>
                  <th scope="col" className="px-1 py-1 font-medium" title="Stocks at a new 52-week high today">
                    New highs
                  </th>
                  <th scope="col" className="px-1 py-1 font-medium" title={`NSE's sector index, change over ${MONTH} sessions`}>
                    Index 1-month
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...classified, ...(rest ? [rest] : [])].map((s) => (
                  <tr key={s.sector ?? "none"} className="border-t border-line/60">
                    <th scope="row" className={`max-w-[11rem] truncate py-1 pr-2 text-left font-normal ${s.sector ? "text-ink" : "text-mute"}`} title={s.sector ?? undefined}>
                      {s.sector ?? "Not classified yet"}
                    </th>
                    <td className="px-1 py-1 text-mute">{fmtInt(s.stocks)}</td>
                    <td className="px-1 py-1">
                      {s.above50_pct === null ? (
                        "—"
                      ) : (
                        <span className="flex items-center gap-1.5">
                          <span className="h-1.5 w-12 overflow-hidden rounded bg-bg" aria-hidden="true">
                            <span className="block h-full bg-accent" style={{ width: `${s.above50_pct}%` }} />
                          </span>
                          {fmtPct(s.above50_pct, 0)}
                        </span>
                      )}
                    </td>
                    <td className={`px-1 py-1 ${tone(s.above50_change)}`}>{pp(s.above50_change)}</td>
                    <td className="px-1 py-1">{fmtNum(s.median_rs, 0)}</td>
                    <td className="px-1 py-1">{fmtInt(s.breakouts)}</td>
                    <td className="px-1 py-1">{fmtInt(s.new_highs)}</td>
                    <td className={`px-1 py-1 ${tone(s.index_change_pct)}`} title={s.index_name ?? "No NSE sector index"}>
                      {fmtPct(s.index_change_pct, 1, true)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rest ? (
            <p className="mt-2 text-[11px] text-mute">
              {fmtInt(rest.stocks)} stocks are not classified yet; NSE&apos;s per-stock sector lookup fills them in later.
            </p>
          ) : null}
        </>
      )}
    </Inner>
  );
}
