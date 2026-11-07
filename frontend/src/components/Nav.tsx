"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { PreferencesButton } from "./Preferences";
import { TopBar } from "./TopBar";

/** Every page, in the order of the side rail (owner, 2026-11-07: a vertical icon bar on the
 *  left, the page names as tooltips; the logo's side menu lists them by name on a narrow screen). */
const PAGES = [
  ["Market Overview", "/dashboard", "home"],
  ["Screener", "/screener", "filter"],
  ["Chart", "/chart", "candles"],
  ["Watchlist", "/watchlist", "star"],
  ["Fundamentals", "/fundamentals", "bars"],
  ["Recent IPOs", "/ipos", "flag"],
  ["Strategies", "/strategies", "layers"],
  ["Paper Trading", "/paper", "wallet"],
  ["Backtest", "/backtest", "rewind"],
  ["Reports", "/reports", "file"],
  ["System Status", "/system", "pulse"],
] as const;

type Page = (typeof PAGES)[number][0];
type IconName = (typeof PAGES)[number][2];

/** Line icons (24 × 24, drawn with the current text colour). */
const ICONS: Record<IconName, React.ReactNode> = {
  home: <path d="M3 11.5 12 4l9 7.5M5.5 9.5V20h13V9.5M10 20v-5.5h4V20" />,
  filter: <path d="M4 5h16l-6.2 7.4V19l-3.6-1.8v-4.8L4 5z" />,
  candles: (
    <>
      <path d="M7 3v4M7 15v6M17 3v3M17 13v8" />
      <rect x="4.5" y="7" width="5" height="8" rx="1" />
      <rect x="14.5" y="6" width="5" height="7" rx="1" />
    </>
  ),
  star: <path d="m12 3.5 2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8L12 3.5z" />,
  bars: <path d="M4 20h16M7 16V11M12 16V6M17 16v-7" />,
  flag: <path d="M5 21V4M5 4h11l-2 4 2 4H5" />,
  layers: <path d="m12 4 9 4.5-9 4.5-9-4.5L12 4zM3 12.5l9 4.5 9-4.5M3 16.5l9 4.5 9-4.5" />,
  wallet: (
    <>
      <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5H18v3M4 7.5V18a2 2 0 0 0 2 2h14V9.5a1.5 1.5 0 0 0-1.5-1.5H6.5A2.5 2.5 0 0 1 4 7.5z" />
      <circle cx="16" cy="14" r="1.2" />
    </>
  ),
  rewind: (
    <>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.5-6M3.5 4v4h4" />
      <path d="M12 8v4.5l3 1.8" />
    </>
  ),
  file: <path d="M6 3h8l4 4v14H6V3zM14 3v4h4M9 12h6M9 16h6" />,
  pulse: <path d="M3 12h4l2.5-6 4 12 2.5-6H21" />,
};

function Icon({ name }: { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

/** The vertical icon rail on the left (from 1024 px): the logo, then one icon per page; its name
 *  shows as a tooltip and is the link's accessible name. */
function Rail({ active }: { active: Page }) {
  return (
    <nav
      aria-label="Main"
      className="fixed inset-y-0 left-0 z-40 hidden w-14 flex-col items-center gap-1 border-r border-frame bg-bg pb-2.5 lg:flex"
    >
      {/* the logo sits in a square as high as the top bar: its line continues the top bar's,
          so the two lines meet the rail's in the top left corner */}
      <a
        href="/dashboard"
        aria-label="ShreeVault home"
        title="ShreeVault"
        className="mb-2 flex h-14 w-full shrink-0 items-center justify-center border-b border-frame hover:bg-panel2"
      >
        <Image src="/shreevault-mark.png" alt="" aria-hidden="true" width={32} height={32} priority className="h-8 w-8 rounded-md" />
      </a>
      {PAGES.map(([label, href, icon]) => {
        const on = active === label;
        return (
          <a
            key={label}
            href={href}
            aria-label={label}
            title={label}
            aria-current={on ? "page" : undefined}
            className={`group relative flex h-10 w-10 items-center justify-center rounded-lg ${
              on ? "bg-accent/15 text-accent" : "text-mute hover:bg-panel2 hover:text-ink"
            }`}
          >
            {on ? <span aria-hidden="true" className="absolute -left-2 h-6 w-0.5 rounded-full bg-accent" /> : null}
            <Icon name={icon} />
          </a>
        );
      })}
    </nav>
  );
}

/** The logo and the name; ``mark`` false: the name only (the rail already shows the mark). */
function Logo({ mark = true }: { mark?: boolean }) {
  return (
    <>
      {mark ? <Image src="/shreevault-mark.png" alt="" aria-hidden="true" width={36} height={36} priority className="h-9 w-9 rounded-md" /> : null}
      <span className="text-left">
        <span className="block text-base font-semibold leading-tight text-ink">ShreeVault</span>
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
    <span aria-label="Today" className="hidden whitespace-nowrap rounded-lg border border-line bg-panel2 px-3 py-1.5 text-xs text-ink sm:block lg:hidden xl:block">
      {today}
    </span>
  ) : null;
}

/** The navigation: the icon rail on the left from 1024 px, and the top bar (logo, search,
 *  preferences, today's date). On a narrow screen the logo opens a side menu with every page. */
export function Nav({
  active = "Market Overview",
  onPick = (symbol) => window.location.assign(`/chart/${encodeURIComponent(symbol)}`),
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
      <header className="sticky top-0 z-30 flex h-14 items-center gap-4 border-b border-frame bg-bg px-4">
        <button
          type="button"
          aria-label="Open the menu"
          aria-expanded={open}
          onClick={() => setOpen(true)}
          className="flex items-center gap-2 rounded-lg px-1 py-1 hover:bg-panel2 lg:hidden"
        >
          <Logo />
        </button>
        <span className="hidden items-center gap-2 lg:flex">
          <Logo mark={false} />
        </span>
        <span className="hidden text-sm text-mute lg:block" aria-hidden="true">
          / {active}
        </span>
        <div className="ml-auto flex items-center gap-3">
          <TopBar onPick={onPick} />
          <PreferencesButton />
          <Today />
        </div>
      </header>
      <Rail active={active} />
      {open ? (
        <div className="fixed inset-0 z-40 flex">
          <aside aria-label="Menu" className="flex h-full w-64 flex-col overflow-y-auto border-r border-line bg-panel p-4 shadow-2xl">
            <div className="mb-6 flex items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <Logo />
              </span>
              <button type="button" aria-label="Close the menu" onClick={() => setOpen(false)} className="rounded px-2 py-1 text-mute hover:bg-panel2 hover:text-ink">
                ✕
              </button>
            </div>
            <ul className="space-y-0.5 text-sm">
              {PAGES.map(([label, href, icon]) => (
                <li key={label}>
                  <a
                    href={href}
                    aria-current={active === label ? "page" : undefined}
                    className={`flex items-center gap-3 rounded-lg px-3 py-2 ${active === label ? "bg-accent font-medium text-white" : "text-ink hover:bg-panel2"}`}
                  >
                    <Icon name={icon} />
                    {label}
                  </a>
                </li>
              ))}
            </ul>
            <p className="mt-auto pt-4 text-[10px] text-mute">Read-only research dashboard</p>
          </aside>
          <button type="button" aria-label="Close the menu" tabIndex={-1} onClick={() => setOpen(false)} className="flex-1 bg-black/50" />
        </div>
      ) : null}
    </>
  );
}
