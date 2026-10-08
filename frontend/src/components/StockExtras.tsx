"use client";

import { useStockHistory, useStockSetups } from "@/lib/api";
import { fmtDay, fmtNum, fmtPrice, strategyLabel } from "@/lib/fmt";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill, TINT } from "./ui";

/** One line per strategy that has this stock on its list; a click shows it on the chart. */
export function StrategiesCard({
  symbol,
  strategy,
  onSelect,
}: {
  symbol: string;
  strategy: string;
  onSelect: (strategy: string) => void;
}) {
  const stock = useStockSetups(symbol);
  const setups = stock.data?.setups ?? [];
  const ranked = setups.filter((s) => s.eligible);
  return (
    <Card
      title="Setups across strategies"
      box={TINT.pink}
      subtitle={stock.data ? `${ranked.length} of ${setups.length} strategies rank this stock` : undefined}
      className="min-w-0"
    >
      {stock.isError ? (
        <ErrorBox error={stock.error} />
      ) : stock.isPending ? (
        <Loading what="setups" />
      ) : setups.length === 0 ? (
        <Empty>No strategy has a setup for {symbol} on this scan date.</Empty>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="text-[11px] uppercase text-mute">
            <tr>
              <th className="px-2 py-1.5 font-medium">Strategy</th>
              <th className="px-2 py-1.5 font-medium">Setup</th>
              <th className="px-2 py-1.5 font-medium">Status</th>
              <th className="px-2 py-1.5 text-right font-medium">Score</th>
              <th className="px-2 py-1.5 text-right font-medium">Pivot</th>
              <th className="px-2 py-1.5 text-right font-medium">Stop</th>
            </tr>
          </thead>
          <tbody>
            {setups.map((s) => (
              <tr key={s.strategy_id} className="border-t border-line">
                <td className="px-2 py-1.5">
                  <button
                    type="button"
                    aria-pressed={s.strategy_id === strategy}
                    onClick={() => onSelect(s.strategy_id)}
                    className={`font-medium hover:underline ${s.strategy_id === strategy ? "text-ink" : "text-accent"}`}
                  >
                    {strategyLabel(s.strategy_id)}
                  </button>
                  {s.eligible ? null : <span className="ml-1 text-mute">(not ranked)</span>}
                </td>
                <td className="px-2 py-1.5">
                  <GradeBadge classification={s.classification} grade={s.grade} />
                </td>
                <td className="px-2 py-1.5">
                  <StatusPill status={s.status} />
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(s.score, 0)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtPrice(s.pivot)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtPrice(s.stop)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

const ICON: Record<string, { sym: string; cls: string; label: string }> = {
  BREAKOUT: { sym: "▲", cls: "text-accent", label: "Breakout" },
  PAPER_ENTRY: { sym: "+", cls: "text-up", label: "Paper entry" },
  PAPER_EXIT: { sym: "−", cls: "text-down", label: "Paper exit" },
  PAPER_SKIPPED_NO_SLOT: { sym: "○", cls: "text-mute", label: "Paper: no free slot" },
  PAPER_DIVERGENCE: { sym: "!", cls: "text-warn", label: "Paper: divergence" },
};

/** Breakouts and paper-ledger events of this stock over the last 180 days, newest first. */
export function HistoryCard({ symbol }: { symbol: string }) {
  const history = useStockHistory(symbol, 180);
  const events = history.data?.events ?? [];
  return (
    <Card title="History and paper trades" box={TINT.orange} subtitle="Last 180 days, newest first" className="min-w-0">
      {history.isError ? (
        <ErrorBox error={history.error} />
      ) : history.isPending ? (
        <Loading what="history" />
      ) : events.length === 0 ? (
        <Empty>No breakout or paper trade for {symbol} in the last 180 days.</Empty>
      ) : (
        <ul className="max-h-72 space-y-2 overflow-auto pr-1 text-xs">
          {events.map((e, i) => {
            const icon = ICON[e.kind] ?? { sym: "•", cls: "text-mute", label: e.kind };
            return (
              <li key={`${e.day}:${e.kind}:${i}`} className="flex gap-2">
                <span aria-hidden className={`w-4 text-center ${icon.cls}`}>
                  {icon.sym}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="text-ink">
                    <strong className="mr-1.5">{icon.label}</strong>
                    {e.text}
                  </span>
                  <span className="block text-[11px] text-mute">
                    {fmtDay(e.day)}
                    {e.strategy_id ? ` · ${strategyLabel(e.strategy_id)}` : ""}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
