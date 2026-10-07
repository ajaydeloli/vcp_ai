"use client";

import { useStatus } from "@/lib/api";
import { DASH, fmtDay, fmtStamp } from "@/lib/fmt";

const LATER = [
  "Watchlist",
  "Market Overview",
  "VCP Scanner",
  "Trend Template",
  "Fundamentals",
  "Alerts",
  "Backtest",
  "Research & Notes",
  "Reports",
  "Settings",
];

function DataStatus() {
  const status = useStatus();
  const s = status.data;
  const scan = s?.strategies.reduce<string | null>(
    (a, x) => (x.latest_scan && (!a || x.latest_scan > a) ? x.latest_scan : a),
    null,
  );
  const rows: [string, string][] = [
    ["Prices to", s ? fmtDay(s.prices_date) : DASH],
    ["Last scan", s ? fmtDay(scan) : DASH],
    ["Data copy", s ? fmtStamp(s.data_time) : DASH],
  ];
  return (
    <div className="rounded-lg border border-line bg-panel2 p-3 text-xs" aria-label="Data status">
      <p className="mb-2 font-medium text-ink">Data status</p>
      <dl className="space-y-1.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between gap-2">
            <dt className="flex items-center gap-1.5 text-mute">
              <span aria-hidden="true" className={`h-2 w-2 rounded-full ${s ? "bg-up" : "bg-mute"}`} />
              {k}
            </dt>
            <dd className="text-right text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function Nav({ active = "Dashboard" }: { active?: "Dashboard" | "Screener" | "Stock Analysis" }) {
  return (
    <nav
      aria-label="Main"
      className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col overflow-y-auto border-r border-line bg-panel p-4 lg:flex"
    >
      <div className="mb-6 flex items-center gap-2">
        <span aria-hidden="true" className="flex h-8 w-8 items-end gap-0.5 rounded bg-accent/20 p-1.5">
          <span className="h-2 w-1 rounded-sm bg-up" />
          <span className="h-4 w-1 rounded-sm bg-accent" />
          <span className="h-3 w-1 rounded-sm bg-warn" />
        </span>
        <div>
          <p className="text-base font-semibold leading-tight text-ink">VCP scanner</p>
          <p className="text-[10px] uppercase tracking-wide text-mute">NSE research dashboard</p>
        </div>
      </div>
      <ul className="space-y-0.5 text-sm">
        {(
          [
            ["Dashboard", "/dashboard"],
            ["Screener", "/screener"],
            ["Stock Analysis", "/stocks"],
          ] as const
        ).map(([label, href]) => (
          <li key={label}>
            <a
              href={href}
              aria-current={active === label ? "page" : undefined}
              className={
                active === label
                  ? "block rounded-lg bg-accent px-3 py-2 font-medium text-white"
                  : "block rounded-lg px-3 py-2 text-ink hover:bg-panel2"
              }
            >
              {label}
            </a>
          </li>
        ))}
        {LATER.map((label) => (
          <li key={label}>
            <span
              aria-disabled="true"
              className="flex cursor-not-allowed items-center justify-between rounded-lg px-3 py-2 text-mute/60"
            >
              {label}
              <span className="text-[10px] uppercase">later</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-auto pt-4">
        <DataStatus />
        <p className="mt-3 text-[10px] text-mute">Read-only research dashboard</p>
      </div>
    </nav>
  );
}
