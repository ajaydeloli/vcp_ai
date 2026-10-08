"use client";

import { useEffect, useState } from "react";
import { useSearch } from "@/lib/api";

export function TopBar({ onPick }: { onPick: (symbol: string) => void }) {
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState(false);
  const hits = useSearch(debounced);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 200);
    return () => clearTimeout(t);
  }, [text]);

  const results = hits.data?.results ?? [];
  const pick = (symbol: string) => {
    onPick(symbol);
    setText("");
    setDebounced("");
    setOpen(false);
    setShown(false);
  };

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="Search symbol or company"
        aria-expanded={shown}
        title="Search"
        onClick={() => setShown((v) => !v)}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-ink hover:bg-panel2 hover:text-accent"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
      </button>
      {shown ? (
      <div className="absolute right-0 top-full z-40 mt-2 w-80 max-w-[90vw]">
        <input
          autoFocus
          type="search"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && results[0]) pick(results[0].symbol);
            if (e.key === "Escape") {
              setOpen(false);
              setShown(false);
            }
          }}
          placeholder="Search symbol or company"
          aria-label="Search symbol or company"
          className="w-full rounded-lg border border-line bg-panel px-3 py-2.5 text-sm text-ink shadow-xl placeholder:text-mute focus:border-accent focus:outline-none"
        />
        {open && debounced ? (
          <ul
            role="listbox"
            aria-label="Search results"
            className="mt-1 max-h-72 w-full overflow-auto rounded-lg border border-line bg-panel p-1 text-sm shadow-xl"
          >
            {hits.isPending ? (
              <li className="px-3 py-2 text-xs text-mute">Searching…</li>
            ) : results.length === 0 ? (
              <li className="px-3 py-2 text-xs text-mute">No match</li>
            ) : (
              results.map((r) => (
                <li key={r.instrument_id} role="option" aria-selected={false}>
                  <button
                    type="button"
                    onClick={() => pick(r.symbol)}
                    className="flex w-full justify-between gap-3 rounded px-3 py-1.5 text-left hover:bg-panel2"
                  >
                    <span className="font-medium text-ink">{r.symbol}</span>
                    <span className="truncate text-xs text-mute">{r.company ?? ""}</span>
                  </button>
                </li>
              ))
            )}
          </ul>
        ) : null}
      </div>
      ) : null}
    </div>
  );
}
