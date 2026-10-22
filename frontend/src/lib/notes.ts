"use client";
// My notes on stocks (owner, 2026-10-22): dated entries per symbol, kept in this browser only
// (localStorage), like the watch lists. Nothing is sent to the server, so the dashboard stays
// read-only. Export to CSV is the backup; a browser that blocks storage keeps nothing.
import { useCallback, useMemo, useSyncExternalStore } from "react";

export const NOTES_KEY = "vcp-stock-notes";
export const MAX_NOTE = 2000;

export type Note = { id: string; at: string; text: string };
export type NotesState = Record<string, Note[]>;

const listeners = new Set<() => void>();

/** A stored value read back safely: anything damaged is dropped. */
export function parseNotes(raw: string): NotesState {
  try {
    const v: unknown = JSON.parse(raw || "{}");
    if (!v || typeof v !== "object" || Array.isArray(v)) return {};
    const out: NotesState = {};
    for (const [sym, list] of Object.entries(v as Record<string, unknown>)) {
      if (!Array.isArray(list)) continue;
      const notes = list.filter(
        (n): n is Note => !!n && typeof n === "object" && typeof (n as Note).id === "string" && typeof (n as Note).at === "string" && typeof (n as Note).text === "string",
      );
      if (notes.length) out[sym.toUpperCase()] = notes;
    }
    return out;
  } catch {
    return {};
  }
}

const readRaw = (): string => {
  try {
    return window.localStorage.getItem(NOTES_KEY) ?? "";
  } catch {
    return "";
  }
};

const save = (state: NotesState) => {
  try {
    window.localStorage.setItem(NOTES_KEY, JSON.stringify(state));
  } catch {
    /* storage blocked: the notes are not kept */
  }
  listeners.forEach((l) => l());
};
const change = (f: (s: NotesState) => NotesState) => save(f(parseNotes(readRaw())));

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
};

/** Every note, newest first within each symbol. */
export function useAllNotes(): NotesState {
  const raw = useSyncExternalStore(subscribe, readRaw, () => "");
  return useMemo(() => parseNotes(raw), [raw]);
}

export function useStockNotes(symbol: string) {
  const all = useAllNotes();
  const sym = symbol.toUpperCase();
  const notes = useMemo(() => [...(all[sym] ?? [])].sort((a, b) => b.at.localeCompare(a.at)), [all, sym]);
  const add = useCallback(
    (text: string) => {
      const t = text.trim().slice(0, MAX_NOTE);
      if (!t) return;
      const note: Note = { id: `n${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`, at: new Date().toISOString(), text: t };
      change((s) => ({ ...s, [sym]: [...(s[sym] ?? []), note] }));
    },
    [sym],
  );
  const remove = useCallback(
    (id: string) =>
      change((s) => {
        const rest = (s[sym] ?? []).filter((n) => n.id !== id);
        const next = { ...s };
        if (rest.length) next[sym] = rest;
        else delete next[sym];
        return next;
      }),
    [sym],
  );
  return { notes, add, remove };
}
