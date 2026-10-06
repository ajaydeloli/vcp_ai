"use client";

import { useStatus } from "@/lib/api";
import { fmtDay, fmtStamp } from "@/lib/fmt";

export const DISCLAIMER = "Research tool, not financial advice";

export function StatusBar() {
  const status = useStatus();
  const s = status.data;
  const scan = s?.strategies.reduce<string | null>(
    (a, x) => (x.latest_scan && (!a || x.latest_scan > a) ? x.latest_scan : a),
    null,
  );
  const warnings = s?.warnings ?? [];

  return (
    <footer className="fixed inset-x-0 bottom-0 z-10 border-t border-line bg-panel/95 px-4 py-2 text-xs backdrop-blur">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
        {status.isError ? (
          <span role="alert" className="text-down">
            {status.error instanceof Error ? status.error.message : "Status unavailable"}
          </span>
        ) : s ? (
          <>
            <span className="text-mute">
              Prices to <span className="text-ink">{fmtDay(s.prices_date)}</span>
            </span>
            <span className="text-mute">
              Scans to <span className="text-ink">{fmtDay(scan)}</span>
            </span>
            <span className="text-mute">
              Data copy written <span className="text-ink">{fmtStamp(s.data_time)}</span>
            </span>
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
          </>
        ) : (
          <span className="text-mute">Loading status…</span>
        )}
        <span className="ml-auto text-mute">{DISCLAIMER}</span>
      </div>
    </footer>
  );
}
