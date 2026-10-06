"use client";

import { useState } from "react";
import { useActivity } from "@/lib/api";
import { fmtDay, strategyLabel } from "@/lib/fmt";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const ICON: Record<string, { sym: string; cls: string }> = {
  BREAKOUT: { sym: "▲", cls: "text-accent" },
  PAPER_ENTRY: { sym: "+", cls: "text-up" },
  PAPER_EXIT: { sym: "−", cls: "text-down" },
  PAPER_SKIPPED_NO_SLOT: { sym: "○", cls: "text-mute" },
  PAPER_DIVERGENCE: { sym: "!", cls: "text-warn" },
  SCAN: { sym: "◎", cls: "text-mute" },
  DAILY_RUN: { sym: "⟳", cls: "text-mute" },
};

export function ActivityPanel() {
  const activity = useActivity(7);
  const [scans, setScans] = useState(false);
  const events = (activity.data?.events ?? []).filter((e) => scans || e.kind !== "SCAN");

  return (
    <Card
      title="Recent activity"
      subtitle="Last 7 days, newest first"
      right={
        <label className="flex items-center gap-1.5 text-[11px] text-mute">
          <input type="checkbox" checked={scans} onChange={(e) => setScans(e.target.checked)} />
          Show scans
        </label>
      }
      className="min-w-0"
    >
      {activity.isError ? (
        <ErrorBox error={activity.error} />
      ) : activity.isPending ? (
        <Loading what="activity" />
      ) : events.length === 0 ? (
        <Empty>Nothing to show in the last 7 days.</Empty>
      ) : (
        <ul className="max-h-72 space-y-2 overflow-auto pr-1 text-xs">
          {events.map((e, i) => {
            const icon = ICON[e.kind] ?? { sym: "•", cls: "text-mute" };
            return (
              <li key={`${e.day}:${e.kind}:${e.symbol ?? ""}:${i}`} className="flex gap-2">
                <span aria-hidden className={`w-4 text-center ${icon.cls}`}>
                  {icon.sym}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="text-ink">
                    {e.symbol ? <strong className="mr-1.5">{e.symbol}</strong> : null}
                    {e.kind === "DAILY_RUN" ? "Daily run: " : ""}
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
