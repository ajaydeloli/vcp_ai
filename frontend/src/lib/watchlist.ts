"use client";
// My watch list: symbols the owner picked, kept in this browser only (localStorage). Nothing is
// sent to the server or written to any database, so the dashboard stays read-only. A browser
// that blocks storage just starts with an empty list.
import { useCallback, useMemo, useSyncExternalStore } from "react";

export const WATCH_KEY = "vcp-my-watchlist";
const listeners = new Set<() => void>();

const readRaw = (): string => {
  try {
    return window.localStorage.getItem(WATCH_KEY) ?? "";
  } catch {
    return "";
  }
};

export const parseList = (raw: string): string[] => {
  try {
    const v: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? [...new Set(v.filter((x): x is string => typeof x === "string"))] : [];
  } catch {
    return [];
  }
};

const write = (symbols: string[]) => {
  try {
    window.localStorage.setItem(WATCH_KEY, JSON.stringify(symbols));
  } catch {
    /* storage blocked: the list is not kept */
  }
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

export function useWatchlist() {
  const raw = useSyncExternalStore(subscribe, readRaw, () => "");
  const symbols = useMemo(() => parseList(raw), [raw]);
  const has = useCallback((s: string) => symbols.includes(s), [symbols]);
  const toggle = useCallback((s: string) => {
    const now = parseList(readRaw());
    write(now.includes(s) ? now.filter((x) => x !== s) : [...now, s]);
  }, []);
  return { symbols, has, toggle };
}
