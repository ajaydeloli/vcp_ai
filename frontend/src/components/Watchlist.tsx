"use client";

import { useEffect, useState } from "react";
import { useScreener, useSearch } from "@/lib/api";
import { fmtDay } from "@/lib/fmt";
import { DEFAULT_QUERY } from "@/lib/screener";
import { useWatchlist } from "@/lib/watchlist";
import { COLS } from "./Screener";
import { Nav } from "./Nav";
import { StatusBar } from "./StatusBar";
import { TopBar } from "./TopBar";
import { Card, Empty, ErrorBox, Loading } from "./ui";

function AddStock() {
  const { has, toggle } = useWatchlist();
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 200);
    return () => clearTimeout(t);
  }, [text]);
  const hits = useSearch(debounced);
  const results = (hits.data?.results ?? []).filter((r) => !has(r.symbol));
  return (
    <div className="relative max-w-sm">
      <input
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Add a stock: symbol or company"
        aria-label="Add a stock to my watch list"
        className="w-full rounded-lg border border-line bg-panel2 px-3 py-2 text-sm text-ink placeholder:text-mute focus:border-accent focus:outline-none"
      />
      {debounced && results.length > 0 ? (
        <ul
          role="listbox"
          aria-label="Stocks to add"
          className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-line bg-panel p-1 text-sm shadow-xl"
        >
          {results.map((r) => (
            <li key={r.instrument_id} role="option" aria-selected={false}>
              <button
                type="button"
                onClick={() => {
                  toggle(r.symbol);
                  setText("");
                }}
                className="flex w-full justify-between gap-3 rounded px-3 py-1.5 text-left hover:bg-panel2"
              >
                <span className="font-medium text-ink">{r.symbol}</span>
                <span className="truncate text-xs text-mute">{r.company ?? ""}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** My watch list: the stocks I starred, each with the latest scan result. Kept in this browser. */
export function Watchlist() {
  const { symbols } = useWatchlist();
  const result = useScreener({ ...DEFAULT_QUERY, symbols, pageSize: 100 }, symbols.length > 0);
  const rows = result.data?.rows ?? [];
  const found = new Set(rows.map((r) => r.symbol));
  const missing = result.data ? symbols.filter((s) => !found.has(s)) : [];

  return (
    <div className="flex min-h-screen">
      <Nav active="Watchlist" />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Watchlist</h1>
        <TopBar onPick={(symbol) => window.location.assign(`/stocks/${encodeURIComponent(symbol)}`)} />
        <Card
          title="My watch list"
          subtitle={
            result.data?.as_of
              ? `${symbols.length} stock${symbols.length === 1 ? "" : "s"} · scan of ${fmtDay(result.data.as_of)}`
              : `${symbols.length} stocks`
          }
          right={<AddStock />}
        >
          {symbols.length === 0 ? (
            <Empty>
              Your list is empty. Press ☆ next to a stock on the Screener or Stock Analysis page, or
              add one with the box above.
            </Empty>
          ) : result.isError ? (
            <ErrorBox error={result.error} />
          ) : !result.data ? (
            <Loading what="watch list" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-[11px] uppercase text-mute">
                  <tr>
                    {COLS.map((c) => (
                      <th key={c.key} scope="col" className={`px-2 py-1.5 font-medium ${c.align === "right" ? "text-right" : ""}`}>
                        {c.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.instrument_id} className="border-t border-line">
                      {COLS.map((c) => (
                        <td key={c.key} className={`px-2 py-1.5 ${c.align === "right" ? "text-right" : ""}`}>
                          {c.render(r)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {missing.length > 0 ? (
            <p className="mt-3 text-xs text-mute">
              Not in the latest scan: {missing.join(", ")}.
            </p>
          ) : null}
        </Card>
        <p className="text-[11px] text-mute">
          This list is kept in this browser only. It is not sent anywhere and does not change any
          scan, score, strategy or the ledger. A research list, not buy signals.
        </p>
      </main>
      <StatusBar />
    </div>
  );
}
