"use client";

import { useState } from "react";
import { useLiveQuotes, useSetups, useStrategies, useSummary } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, strategyLabel, tone } from "@/lib/fmt";
import { Page } from "./Page";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill, TINT } from "./ui";

const STATUSES = [
  ["", "All"],
  ["BREAKOUT", "Breakout"],
  ["PIVOT_READY", "Pivot ready"],
  ["FORMING", "Forming"],
] as const;

const TH = "py-2 px-2 text-center font-medium";
const TD = "py-2 px-2 text-center tabular-nums";

/** Today's setups of one strategy, from the daily scan. A filter for research, not buy signals. */
export function VcpScanner() {
  const strategies = useStrategies();
  const summary = useSummary();
  const ids = strategies.data?.strategies.map((s) => s.strategy_id) ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const strategy = picked ?? ids[0] ?? "vcp";
  const [status, setStatus] = useState("");
  const setups = useSetups(strategy, status || undefined, ids.length > 0);
  const rows = setups.data?.rows ?? [];
  const live = useLiveQuotes(rows.map((r) => r.symbol), rows.length > 0);
  const counts = summary.data?.strategies.find((s) => s.strategy_id === strategy);

  return (
    <Page active="VCP Scanner" title="VCP Scanner">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Ranked", counts?.ranked, TINT.blue],
          ["Grade 2 or better", counts?.grade2_plus, TINT.green],
          ["Breakouts", counts?.breakouts, TINT.pink],
          ["Mean score, top 10", counts?.mean_top10_score, TINT.amber],
        ].map(([label, value, box]) => (
          <Card key={String(label)} label={String(label)} box={String(box)}>
            <p className="text-xs text-mute">{String(label)}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">
              {typeof value === "number" ? (label === "Mean score, top 10" ? fmtNum(value, 1) : fmtInt(value)) : DASH}
            </p>
          </Card>
        ))}
      </div>

      <Card
        title="Setups"
        subtitle={
          setups.data?.as_of
            ? `Scan of ${fmtDay(setups.data.as_of)}. A filter for research, not buy signals.`
            : "A filter for research, not buy signals."
        }
        box={TINT.pink}
        right={
          <span className="text-xs text-mute">
            {rows.length} setup{rows.length === 1 ? "" : "s"}
          </span>
        }
      >
        <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
          <div role="group" aria-label="Strategy" className="flex flex-wrap items-center gap-1">
            <span className="text-mute">Strategy :</span>
            {ids.map((id) => (
              <button
                key={id}
                type="button"
                aria-pressed={id === strategy}
                onClick={() => setPicked(id)}
                className={`rounded border px-2 py-1 ${id === strategy ? "border-accent bg-accent/15 text-accent" : "border-line text-ink hover:bg-panel2"}`}
              >
                {strategyLabel(id)}
              </button>
            ))}
          </div>
          <div role="group" aria-label="Status" className="flex flex-wrap items-center gap-1">
            <span className="text-mute">Status :</span>
            {STATUSES.map(([value, label]) => (
              <button
                key={label}
                type="button"
                aria-pressed={value === status}
                onClick={() => setStatus(value)}
                className={`rounded border px-2 py-1 ${value === status ? "border-accent bg-accent/15 text-accent" : "border-line text-ink hover:bg-panel2"}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {setups.isError ? (
          <ErrorBox error={setups.error} />
        ) : setups.isPending ? (
          <Loading what="setups" />
        ) : rows.length === 0 ? (
          <Empty>No setups for this strategy and status.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[11px] uppercase text-mute">
                <tr>
                  <th className={TH}>#</th>
                  <th className="px-2 py-2 text-left font-medium">Symbol</th>
                  <th className={TH}>Setup</th>
                  <th className={TH}>Status</th>
                  <th className={TH}>Score</th>
                  <th className={TH}>RS rank</th>
                  <th className={TH}>Close</th>
                  <th className={TH}>Change</th>
                  <th className={TH}>Pivot</th>
                  <th className={TH}>To pivot</th>
                  <th className={TH}>Stop</th>
                  <th className={TH}>Base days</th>
                  <th className={TH}>Base depth</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const q = live.bySymbol.get(r.symbol);
                  const last = q?.available ? q.last_price : null;
                  return (
                    <tr key={r.instrument_id} className="border-t border-line/60">
                      <td className={`${TD} text-mute`}>{i + 1}</td>
                      <td className="px-2 py-2 text-left">
                        <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium text-ink hover:text-accent">
                          {r.symbol}
                        </a>
                        <span className="block max-w-[14rem] truncate text-[11px] text-mute">{r.company ?? DASH}</span>
                      </td>
                      <td className={TD}>
                        <GradeBadge classification={r.classification} grade={r.grade} />
                      </td>
                      <td className={TD}>
                        <StatusPill status={r.status} />
                      </td>
                      <td className={TD}>{fmtNum(r.score, 1)}</td>
                      <td className={TD}>{fmtNum(r.rs_rank, 0)}</td>
                      <td className={TD} title={last !== null ? "Live price, display only" : "Last close"}>
                        {fmtPrice(last ?? r.close)}
                      </td>
                      <td className={`${TD} ${tone(q?.available ? q.change_pct : r.change_pct)}`}>
                        {fmtPct(q?.available ? q.change_pct : r.change_pct, 2, true)}
                      </td>
                      <td className={TD}>{fmtPrice(r.pivot)}</td>
                      <td className={TD}>{fmtPct(r.pivot_distance_pct, 1, true)}</td>
                      <td className={TD}>{fmtPrice(r.stop)}</td>
                      <td className={TD}>{fmtInt(r.base_days)}</td>
                      <td className={TD}>{fmtPct(r.base_depth_pct, 1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </Page>
  );
}
