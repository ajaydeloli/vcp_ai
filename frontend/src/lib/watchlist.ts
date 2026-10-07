"use client";
// My watch lists: several named lists of symbols the owner made, kept in this browser only
// (localStorage). Nothing is sent to the server or written to any database, so the dashboard
// stays read-only. A browser that blocks storage starts with the one empty default list.
import { useCallback, useMemo, useSyncExternalStore } from "react";

export const WATCH_KEY = "vcp-my-watchlists";
const OLD_KEY = "vcp-my-watchlist"; // first design: one plain list of symbols
export const DEFAULT_NAME = "My watch list";

export type WatchList = { id: string; name: string; symbols: string[] };
export type WatchState = { lists: WatchList[]; active: string };

const listeners = new Set<() => void>();
const str = (x: unknown): x is string => typeof x === "string";
const uniq = (a: unknown[]): string[] => [...new Set(a.filter(str))];

const fresh = (symbols: string[] = []): WatchState => ({
  lists: [{ id: "l1", name: DEFAULT_NAME, symbols }],
  active: "l1",
});

/** A stored value read back safely: anything damaged becomes the default list. */
export function parseState(raw: string, old = ""): WatchState {
  try {
    const v: unknown = raw ? JSON.parse(raw) : null;
    if (v && typeof v === "object" && Array.isArray((v as WatchState).lists)) {
      const lists = (v as WatchState).lists.flatMap((l) =>
        l && str(l.id) && str(l.name) && Array.isArray(l.symbols)
          ? [{ id: l.id, name: l.name.slice(0, 40), symbols: uniq(l.symbols) }]
          : [],
      );
      if (lists.length > 0) {
        const active = lists.some((l) => l.id === (v as WatchState).active)
          ? (v as WatchState).active
          : lists[0]!.id;
        return { lists, active };
      }
    }
  } catch {
    /* fall through to the default */
  }
  try {
    const o: unknown = old ? JSON.parse(old) : [];
    return fresh(Array.isArray(o) ? uniq(o) : []);
  } catch {
    return fresh();
  }
}

const read = (key: string): string => {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
};
const readRaw = (): string => read(WATCH_KEY) || `old:${read(OLD_KEY)}`;
const stateOf = (raw: string): WatchState =>
  raw.startsWith("old:") ? parseState("", raw.slice(4)) : parseState(raw);

const save = (state: WatchState) => {
  try {
    window.localStorage.setItem(WATCH_KEY, JSON.stringify(state));
  } catch {
    /* storage blocked: the lists are not kept */
  }
  listeners.forEach((l) => l());
};
const change = (f: (s: WatchState) => WatchState) => save(f(stateOf(readRaw())));

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
};

export function useWatchlists() {
  const raw = useSyncExternalStore(subscribe, readRaw, () => "");
  const state = useMemo(() => stateOf(raw), [raw]);
  const active = state.lists.find((l) => l.id === state.active) ?? state.lists[0]!;
  const create = useCallback((name: string) => {
    const id = `l${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
    change((s) => ({
      lists: [...s.lists, { id, name: name.trim().slice(0, 40) || "New list", symbols: [] }],
      active: id,
    }));
    return id;
  }, []);
  const rename = useCallback(
    (id: string, name: string) =>
      change((s) => ({
        ...s,
        lists: s.lists.map((l) => (l.id === id ? { ...l, name: name.trim().slice(0, 40) || l.name } : l)),
      })),
    [],
  );
  const remove = useCallback(
    (id: string) =>
      change((s) => {
        const lists = s.lists.filter((l) => l.id !== id);
        return lists.length === 0 ? fresh() : { lists, active: lists.some((l) => l.id === s.active) ? s.active : lists[0]!.id };
      }),
    [],
  );
  const setActive = useCallback((id: string) => change((s) => ({ ...s, active: id })), []);
  /** add the symbol to the list, or take it out when it is already there */
  const toggle = useCallback(
    (id: string, symbol: string) =>
      change((s) => ({
        ...s,
        lists: s.lists.map((l) =>
          l.id !== id
            ? l
            : { ...l, symbols: l.symbols.includes(symbol) ? l.symbols.filter((x) => x !== symbol) : [...l.symbols, symbol] },
        ),
      })),
    [],
  );
  const listsOf = useCallback(
    (symbol: string) => state.lists.filter((l) => l.symbols.includes(symbol)),
    [state],
  );
  return { lists: state.lists, active, create, rename, remove, setActive, toggle, listsOf };
}
