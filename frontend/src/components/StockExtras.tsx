"use client";

import { useStockHistory } from "@/lib/api";
import { fmtDay, strategyLabel } from "@/lib/fmt";
import { Card, Empty, ErrorBox, Loading, TINT } from "./ui";

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
