"use client";

import { useState } from "react";
import { useWatchlists } from "@/lib/watchlist";

/** ★ button: choose which of my watch lists hold this stock (kept in this browser). */
export function WatchStar({ symbol }: { symbol: string }) {
  const { lists, create, toggle, listsOf } = useWatchlists();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const on = listsOf(symbol).length > 0;
  return (
    <span className="relative inline-block">
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Watch lists of ${symbol}`}
        title={on ? `In ${listsOf(symbol).map((l) => l.name).join(", ")}` : "Add to a watch list"}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        className={`text-base leading-none ${on ? "text-warn" : "text-mute hover:text-ink"}`}
      >
        {on ? "★" : "☆"}
      </button>
      {open ? (
        <div
          role="group"
          aria-label={`Watch lists for ${symbol}`}
          className="absolute left-0 z-30 mt-1 w-52 rounded-lg border border-line bg-panel p-2 text-xs shadow-xl"
        >
          {lists.map((l) => (
            <label key={l.id} className="flex items-center gap-2 rounded px-1 py-1 text-ink hover:bg-panel2">
              <input type="checkbox" checked={l.symbols.includes(symbol)} onChange={() => toggle(l.id, symbol)} />
              <span className="truncate">{l.name}</span>
            </label>
          ))}
          <form
            className="mt-1 flex gap-1 border-t border-line pt-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim()) return;
              toggle(create(name), symbol);
              setName("");
            }}
          >
            <input
              value={name}
              maxLength={40}
              onChange={(e) => setName(e.target.value)}
              placeholder="New list name"
              aria-label="New watch list name"
              className="min-w-0 flex-1 rounded border border-line bg-panel2 px-2 py-1 text-ink"
            />
            <button type="submit" className="rounded border border-line px-2 py-1 text-ink hover:bg-panel2">
              Add
            </button>
          </form>
        </div>
      ) : null}
    </span>
  );
}
