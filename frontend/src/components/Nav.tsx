"use client";

import { useEffect, useState } from "react";
import { TopBar } from "./TopBar";

const LATER = [
  "VCP Scanner",
  "Trend Template",
  "Fundamentals",
  "Alerts",
  "Backtest",
  "Research & Notes",
  "Reports",
  "Settings",
];

const PAGES = [
  ["Dashboard", "/dashboard"],
  ["Screener", "/screener"],
  ["Stock Analysis", "/stocks"],
  ["Watchlist", "/watchlist"],
] as const;

type Page = (typeof PAGES)[number][0];

function Logo() {
  return (
    <>
      <span aria-hidden="true" className="flex h-8 w-8 items-end gap-0.5 rounded bg-accent/20 p-1.5">
        <span className="h-2 w-1 rounded-sm bg-up" />
        <span className="h-4 w-1 rounded-sm bg-accent" />
        <span className="h-3 w-1 rounded-sm bg-warn" />
      </span>
      <span className="text-left">
        <span className="block text-base font-semibold leading-tight text-ink">VCP scanner</span>
        <span className="block text-[10px] uppercase tracking-wide text-mute">NSE research dashboard</span>
      </span>
    </>
  );
}

/** Today's date in India, set after the page loads so the server and the browser cannot disagree. */
function Today() {
  const [today, setToday] = useState<string | null>(null);
  useEffect(() => {
    setToday(
      new Intl.DateTimeFormat("en-IN", {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "Asia/Kolkata",
      }).format(new Date()),
    );
  }, []);
  return today ? (
    <span aria-label="Today" className="hidden whitespace-nowrap rounded-lg border border-line bg-panel2 px-3 py-1.5 text-xs text-ink sm:block">
      {today}
    </span>
  ) : null;
}

/** The top navigation bar. The logo opens a side bar with every page, including those still to come. */
export function Nav({
  active = "Dashboard",
  onPick = (symbol) => window.location.assign(`/stocks/${encodeURIComponent(symbol)}`),
}: {
  active?: Page;
  onPick?: (symbol: string) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <header className="sticky top-0 z-30 flex h-14 items-center gap-4 border-b border-line bg-panel px-4">
        <button
          type="button"
          aria-label="Open the menu"
          aria-expanded={open}
          onClick={() => setOpen(true)}
          className="flex items-center gap-2 rounded-lg px-1 py-1 hover:bg-panel2"
        >
          <Logo />
        </button>
        <nav aria-label="Main" className="hidden h-full items-stretch gap-1 text-sm md:flex">
          {PAGES.map(([label, href]) => (
            <a
              key={label}
              href={href}
              aria-current={active === label ? "page" : undefined}
              className={
                active === label
                  ? "flex items-center border-b-2 border-accent px-3 font-medium text-accent"
                  : "flex items-center border-b-2 border-transparent px-3 text-ink hover:text-accent"
              }
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <TopBar onPick={onPick} />
          <Today />
        </div>
      </header>
      {open ? (
        <div className="fixed inset-0 z-40 flex">
          <aside
            aria-label="Menu"
            className="flex h-full w-64 flex-col overflow-y-auto border-r border-line bg-panel p-4 shadow-2xl"
          >
            <div className="mb-6 flex items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <Logo />
              </span>
              <button
                type="button"
                aria-label="Close the menu"
                onClick={() => setOpen(false)}
                className="rounded px-2 py-1 text-mute hover:bg-panel2 hover:text-ink"
              >
                ✕
              </button>
            </div>
            <ul className="space-y-0.5 text-sm">
              {PAGES.map(([label, href]) => (
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
            <p className="mt-auto pt-4 text-[10px] text-mute">Read-only research dashboard</p>
          </aside>
          <button
            type="button"
            aria-label="Close the menu"
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="flex-1 bg-black/50"
          />
        </div>
      ) : null}
    </>
  );
}
