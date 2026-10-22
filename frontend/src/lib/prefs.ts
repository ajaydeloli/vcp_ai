"use client";
// My preferences (owner, 2026-10-22): kept in this browser only (localStorage), like the watch
// lists. They change how pages look and start, never what a scan or the API computes.
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { resetNumberLocale } from "./numberStyle";
import { PREFS_KEY } from "./themeBoot";

export { PREFS_KEY };

export type Theme = "dark" | "light" | "system";
export type Numbers = "in" | "intl";
export type Prefs = {
  theme: Theme;
  /** strategy the Screener opens with */
  strategy: string;
  /** Screener rows per page */
  pageSize: number;
  /** Screener columns hidden by default (column keys) */
  hiddenCols: string[];
  /** Screener: show the EPS and sales growth columns from the start */
  showFundamentals: boolean;
  /** 1,23,456.78 (Indian) or 123,456.78 (international) */
  numbers: Numbers;
  /** the page the address without a path (and the logo's Home) opens */
  startPage: string;
};

export const PAGE_SIZES = [15, 25, 50, 100];
export const START_PAGES: [string, string][] = [
  ["/dashboard", "Market Overview"],
  ["/screener", "Screener"],
  ["/trend-template", "Trend Template"],
  ["/watchlist", "Watchlist"],
  ["/stocks", "Stock Analysis"],
  ["/strategies", "Strategies"],
];

export const DEFAULT_PREFS: Prefs = {
  theme: "dark",
  strategy: "vcp",
  pageSize: 15,
  hiddenCols: [],
  showFundamentals: false,
  numbers: "in",
  startPage: "/dashboard",
};

const listeners = new Set<() => void>();

/** A stored value read back safely: anything missing or damaged takes its default. */
export function parsePrefs(raw: string): Prefs {
  let v: Record<string, unknown> = {};
  try {
    const x: unknown = JSON.parse(raw || "{}");
    if (x && typeof x === "object" && !Array.isArray(x)) v = x as Record<string, unknown>;
  } catch {
    /* damaged: defaults */
  }
  const pick = <T>(ok: boolean, value: unknown, fallback: T): T => (ok ? (value as T) : fallback);
  return {
    theme: pick(["dark", "light", "system"].includes(v.theme as string), v.theme, DEFAULT_PREFS.theme),
    strategy: pick(typeof v.strategy === "string" && /^[a-z_]{1,40}$/.test(v.strategy), v.strategy, DEFAULT_PREFS.strategy),
    pageSize: pick(PAGE_SIZES.includes(v.pageSize as number), v.pageSize, DEFAULT_PREFS.pageSize),
    hiddenCols: Array.isArray(v.hiddenCols) ? v.hiddenCols.filter((c): c is string => typeof c === "string") : DEFAULT_PREFS.hiddenCols,
    showFundamentals: pick(typeof v.showFundamentals === "boolean", v.showFundamentals, DEFAULT_PREFS.showFundamentals),
    numbers: pick(v.numbers === "in" || v.numbers === "intl", v.numbers, DEFAULT_PREFS.numbers),
    startPage: pick(
      START_PAGES.some(([p]) => p === v.startPage),
      v.startPage,
      DEFAULT_PREFS.startPage,
    ),
  };
}

const readRaw = (): string => {
  try {
    return window.localStorage.getItem(PREFS_KEY) ?? "";
  } catch {
    return "";
  }
};

/** The preferences now, outside React (number formatting, the first paint). */
export const currentPrefs = (): Prefs => (typeof window === "undefined" ? DEFAULT_PREFS : parsePrefs(readRaw()));

const save = (p: Prefs) => {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* storage blocked: the preferences last until the page is reloaded */
  }
  resetNumberLocale();
  applyTheme(p.theme);
  listeners.forEach((l) => l());
};

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
};

export function usePrefs() {
  const raw = useSyncExternalStore(subscribe, readRaw, () => "");
  const prefs = useMemo(() => parsePrefs(raw), [raw]);
  const set = useCallback((change: Partial<Prefs>) => save({ ...parsePrefs(readRaw()), ...change }), []);
  const reset = useCallback(() => save(DEFAULT_PREFS), []);
  return { prefs, set, reset };
}

// --- theme --------------------------------------------------------------------------------

const systemDark = () => typeof window !== "undefined" && !!window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;

export const resolveTheme = (t: Theme): "dark" | "light" => (t === "system" ? (systemDark() ? "dark" : "light") : t);

export function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  const resolved = resolveTheme(t);
  if (resolved === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  window.dispatchEvent(new Event("vcp-theme"));
}

/** "dark" or "light" now; changes when the preference or (for "system") the computer's setting does. */
export function useResolvedTheme(): "dark" | "light" {
  const { prefs } = usePrefs();
  const [, bump] = useState(0);
  useEffect(() => {
    const on = () => bump((n) => n + 1);
    // the computer switched dark/light: follow it when the preference is "system"
    const sys = () => (currentPrefs().theme === "system" ? applyTheme("system") : undefined);
    const mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    mq?.addEventListener?.("change", sys);
    window.addEventListener("vcp-theme", on);
    return () => {
      mq?.removeEventListener?.("change", sys);
      window.removeEventListener("vcp-theme", on);
    };
  }, []);
  return resolveTheme(prefs.theme);
}

/** A theme colour as a CSS colour string for canvas charts ("rgb(13, 20, 34)"). */
export function themeColor(name: string, alpha = 1): string {
  if (typeof document === "undefined") return "rgb(128, 128, 128)";
  const v = getComputedStyle(document.documentElement).getPropertyValue(`--c-${name}`).trim();
  const [r = "128", g = "128", b = "128"] = v.split(/\s+/);
  return alpha >= 1 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
