"use client";

import { useEffect, useState } from "react";
import { useStatus } from "@/lib/api";
import { fmtDay, fmtStamp } from "@/lib/fmt";

export const DISCLAIMER = "Research tool, not financial advice";

function Chip({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <span className="flex items-center gap-1.5 whitespace-nowrap">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${ok ? "bg-up" : "bg-mute"}`} />
      <span className="text-mute">{label}</span>
      <span className="text-ink">{value}</span>
    </span>
  );
}

/** Today's date in India, set after the page loads so the server and the browser cannot disagree. */
function useToday(): string | null {
  const [today, setToday] = useState<string | null>(null);
  useEffect(() => {
    const t = new Intl.DateTimeFormat("en-IN", {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "Asia/Kolkata",
    }).format(new Date());
    setToday(t);
  }, []);
  return today;
}

export function StatusBar() {
  const status = useStatus();
  const s = status.data;
  const warnings = s?.warnings ?? [];
  const today = useToday();
  const scan = s?.strategies.reduce<string | null>(
    (a, x) => (x.latest_scan && (!a || x.latest_scan > a) ? x.latest_scan : a),
    null,
  );

  return (
    <footer className="fixed inset-x-0 bottom-0 z-10 border-t border-line bg-panel/95 px-4 py-2 text-xs backdrop-blur">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
        {status.isError ? (
          <span role="alert" className="text-down">
            {status.error instanceof Error ? status.error.message : "Status unavailable"}
          </span>
        ) : s ? (
          <>
            {warnings.length === 0 ? (
              <span className="text-up">No warnings</span>
            ) : (
              <details className="text-warn">
                <summary className="cursor-pointer">
                  {warnings.length} warning{warnings.length > 1 ? "s" : ""}
                </summary>
                <ul className="mt-1 list-disc pl-5">
                  {warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </details>
            )}
            <span aria-label="Data status" className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <Chip label="Prices to" value={fmtDay(s.prices_date)} ok />
              <Chip label="Last scan" value={fmtDay(scan)} ok />
              <Chip label="Data copy" value={fmtStamp(s.data_time)} ok />
            </span>
          </>
        ) : (
          <span className="text-mute">Loading status…</span>
        )}
        {today ? (
          <span className="rounded border border-line bg-panel2 px-2 py-0.5 text-ink" aria-label="Today">
            {today}
          </span>
        ) : null}
        <span className="ml-auto text-mute">{DISCLAIMER}</span>
      </div>
    </footer>
  );
}
