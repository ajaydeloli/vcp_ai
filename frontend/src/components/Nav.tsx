"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { PreferencesButton } from "./Preferences";
import { TopBar } from "./TopBar";

/** On the top bar (owner, 2026-10-23). */
const TOP = [
  ["Market Overview", "/dashboard"],
  ["Screener", "/screener"],
  ["Trend Template", "/trend-template"],
  ["Stock Analysis", "/stocks"],
  ["Watchlist", "/watchlist"],
  ["Past Leaders", "/leaders"],
] as const;

/** In the top bar's More dropdown. */
const MORE = [
  ["Fundamentals", "/fundamentals"],
  ["Recent IPOs", "/ipos"],
  ["Strategies", "/strategies"],
  ["Paper Trading", "/paper"],
  ["Backtest", "/backtest"],
  ["Reports", "/reports"],
  ["Alerts", "/alerts"],
  ["AI Agent", "/agent"],
  ["System Status", "/system"],
] as const;

type Page = (typeof TOP)[number][0] | (typeof MORE)[number][0];

const TOP_LINK = "flex items-center whitespace-nowrap border-b-2 px-2.5 xl:px-3";
const TOP_ON = `${TOP_LINK} border-accent font-medium text-accent`;
const TOP_OFF = `${TOP_LINK} border-transparent text-ink hover:text-accent`;

/** The More dropdown: a button that shows the other pages. Keyboard: Enter / Space / ArrowDown
 *  open it, ArrowUp / ArrowDown / Home / End move between its links, Escape closes it and returns
 *  to the button; a click outside or moving the focus away closes it. When the current page is in
 *  it, the button is highlighted (and names the page on a wide screen) and the link is marked. */
function MoreMenu({ active }: { active: Page }) {
  const [open, setOpen] = useState(false);
  const [focusFirst, setFocusFirst] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const current = MORE.find(([label]) => label === active)?.[0] ?? null;
  const links = () => Array.from(wrap.current?.querySelectorAll<HTMLAnchorElement>("a[data-more]") ?? []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (open && focusFirst) {
      links()[0]?.focus();
      setFocusFirst(false);
    }
  }, [open, focusFirst]);

  const onListKey = (e: React.KeyboardEvent) => {
    const all = links();
    const at = all.indexOf(document.activeElement as HTMLAnchorElement);
    const go = (i: number) => {
      e.preventDefault();
      all[(i + all.length) % all.length]?.focus();
    };
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(all.length - 1);
  };

  return (
    <div
      ref={wrap}
      className="relative flex items-stretch"
      onBlur={(e) => {
        if (!wrap.current?.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        ref={button}
        type="button"
        aria-label={current ? `More pages, current: ${current}` : "More pages"}
        aria-expanded={open}
        aria-controls="nav-more"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setFocusFirst(true);
          }
        }}
        className={current ? TOP_ON : TOP_OFF}
      >
        More
        {current ? <span className="hidden xl:inline">{` · ${current}`}</span> : null}
        <span aria-hidden="true" className="ml-1 text-xs">
          ▾
        </span>
      </button>
      {open ? (
        <ul
          id="nav-more"
          aria-label="More pages"
          onKeyDown={onListKey}
          className="absolute left-0 top-full z-40 min-w-48 rounded-b-lg border border-line bg-panel py-1 text-sm shadow-xl"
        >
          {MORE.map(([label, href]) => (
            <li key={label}>
              <a
                data-more
                href={href}
                aria-current={active === label ? "page" : undefined}
                className={active === label ? "block px-4 py-2 font-medium text-accent bg-accent/10" : "block px-4 py-2 text-ink hover:bg-panel2 focus:bg-panel2"}
              >
                {label}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Logo() {
  return (
    <>
      <Image src="/shreevault-mark.png" alt="" aria-hidden="true" width={36} height={36} priority className="h-9 w-9 rounded-md" />
      <span className="text-left">
        <span className="block text-base font-semibold leading-tight text-ink">ShreeVault</span>
        <span className="block text-[10px] uppercase tracking-wide text-mute">NSE VCP scanner</span>
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

/** The top navigation bar: the main pages and a More dropdown from 1024 px; the logo opens a side
 *  bar with every page (the only menu on a narrow screen). */
export function Nav({
  active = "Market Overview",
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
        <nav aria-label="Main" className="hidden h-full items-stretch gap-1 text-sm lg:flex">
          {TOP.map(([label, href]) => (
            <a key={label} href={href} aria-current={active === label ? "page" : undefined} className={active === label ? TOP_ON : TOP_OFF}>
              {label}
            </a>
          ))}
          <MoreMenu active={active} />
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <TopBar onPick={onPick} />
          <PreferencesButton />
          <Today />
        </div>
      </header>
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
              {[...TOP, null, ...MORE].map((page) => {
                if (page === null)
                  return (
                    <li key="more" aria-hidden="true" className="px-3 pb-1 pt-3 text-[10px] uppercase tracking-wide text-mute">
                      More
                    </li>
                  );
                const [label, href] = page;
                return (
                  <li key={label}>
                    <a
                      href={href}
                      aria-current={active === label ? "page" : undefined}
                      className={active === label ? "block rounded-lg bg-accent px-3 py-2 font-medium text-white" : "block rounded-lg px-3 py-2 text-ink hover:bg-panel2"}
                    >
                      {label}
                    </a>
                  </li>
                );
              })}
            </ul>
            <p className="mt-auto pt-4 text-[10px] text-mute">Read-only research dashboard</p>
          </aside>
          <button type="button" aria-label="Close the menu" tabIndex={-1} onClick={() => setOpen(false)} className="flex-1 bg-black/50" />
        </div>
      ) : null}
    </>
  );
}
