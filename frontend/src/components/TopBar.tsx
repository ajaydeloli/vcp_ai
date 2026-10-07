"use client";

import { useEffect, useState } from "react";
import { useMarket, useSearch, useStatus } from "@/lib/api";
import { fmtDay, fmtNum, fmtPct, tone } from "@/lib/fmt";
import { Sparkline } from "./Rings";

/** Our own equal-weight index of the scanned universe (not NIFTY: no index data is stored). */
function UniverseIndexTile() {
  const market = useMarket();
  const days = market.data?.days ?? [];
  const last = days[days.length - 1];
  const prev = days[days.length - 2];
  const change =
    last?.index != null && prev?.index != null && prev.index !== 0 ? (last.index / prev.index - 1) * 100 : null;
  return (
    <div
      className="flex h-full items-center gap-3 rounded-lg border border-line bg-panel px-3 py-2"
      title="Equal-weight index of the stocks we scan, 100 on the first day shown. Not NIFTY."
    >
      <div>
        <p className="text-[10px] uppercase tracking-wide text-mute">Our NSE universe index</p>
        <p className="text-lg font-semibold leading-tight tabular-nums text-ink">
          {fmtNum(last?.index, 2)}
          <span className={`ml-2 text-xs font-normal ${tone(change)}`}>{fmtPct(change, 2, true)}</span>
        </p>
      </div>
      <Sparkline values={days.slice(-60).flatMap((d) => (d.index === null ? [] : [d.index]))} color="#26c281" />
    </div>
  );
}

export function TopBar({ onPick }: { onPick: (symbol: string) => void }) {
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const status = useStatus();
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
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="relative w-full max-w-md">
        <input
          type="search"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && results[0]) pick(results[0].symbol);
            if (e.key === "Escape") setOpen(false);
          }}
          placeholder="Search symbol or company"
          aria-label="Search symbol or company"
          className="w-full rounded-lg border border-line bg-panel px-3 py-2.5 text-sm text-ink placeholder:text-mute focus:border-accent focus:outline-none"
        />
        {open && debounced ? (
          <ul
            role="listbox"
            aria-label="Search results"
            className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-line bg-panel p-1 text-sm shadow-xl"
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
      <div className="flex flex-wrap items-stretch gap-3">
        <UniverseIndexTile />
        <div className="flex flex-col justify-center whitespace-nowrap rounded-lg border border-line bg-panel px-3 py-2">
          <p className="text-[10px] uppercase tracking-wide text-mute">Data of</p>
          <p className="text-lg font-semibold leading-tight tabular-nums text-ink">
            {fmtDay(status.data?.prices_date)}
          </p>
          <p className="text-xs text-mute">end of day, IST</p>
        </div>
      </div>
    </div>
  );
}
