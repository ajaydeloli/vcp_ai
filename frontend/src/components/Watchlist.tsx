"use client";

import { useEffect, useState } from "react";
import { useLiveQuotes, useScreener, useSearch } from "@/lib/api";
import { fmtDay } from "@/lib/fmt";
import { DEFAULT_QUERY } from "@/lib/screener";
import { useWatchlists } from "@/lib/watchlist";
import { alignClass, COLS } from "./Screener";
import { Nav } from "./Nav";
import { StatusBar } from "./StatusBar";
import { Card, Empty, ErrorBox, Loading } from "./ui";

function AddStock() {
  const { active, toggle } = useWatchlists();
  const has = (s: string) => active.symbols.includes(s);
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
        placeholder={`Add to ${active.name}: symbol or company`}
        aria-label="Add a stock to this list"
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
                  toggle(active.id, r.symbol);
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

function Tabs() {
  const { lists, active, setActive, create, rename, remove } = useWatchlists();
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [text, setText] = useState("");
  const input = "rounded border border-line bg-panel2 px-2 py-1 text-xs text-ink";
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2" role="tablist" aria-label="My watch lists">
      {lists.map((l) => (
        <button
          key={l.id}
          type="button"
          role="tab"
          aria-selected={l.id === active.id}
          onClick={() => {
            setActive(l.id);
            setRenaming(false);
            setConfirm(false);
          }}
          className={`rounded-lg border px-3 py-1.5 text-xs ${
            l.id === active.id ? "border-accent bg-accent/10 text-ink" : "border-line text-mute hover:text-ink"
          }`}
        >
          {l.name} ({l.symbols.length})
        </button>
      ))}
      {adding ? (
        <form
          className="flex gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) create(text);
            setText("");
            setAdding(false);
          }}
        >
          <input autoFocus value={text} maxLength={40} onChange={(e) => setText(e.target.value)} placeholder="List name" aria-label="New list name" className={input} />
          <button type="submit" className={`${input} hover:bg-panel`}>
            Create
          </button>
        </form>
      ) : (
        <button type="button" onClick={() => setAdding(true)} className="rounded-lg border border-dashed border-line px-3 py-1.5 text-xs text-accent hover:bg-panel2">
          + New watchlist
        </button>
      )}
      <span className="ml-auto flex items-center gap-2 text-xs">
        {renaming ? (
          <form
            className="flex gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              rename(active.id, text);
              setText("");
              setRenaming(false);
            }}
          >
            <input autoFocus value={text} maxLength={40} onChange={(e) => setText(e.target.value)} aria-label="List name" className={input} />
            <button type="submit" className={`${input} hover:bg-panel`}>
              Save
            </button>
          </form>
        ) : (
          <button
            type="button"
            onClick={() => {
              setText(active.name);
              setRenaming(true);
            }}
            className="text-mute hover:text-ink"
          >
            Rename
          </button>
        )}
        {confirm ? (
          <>
            <span className="text-down">Delete “{active.name}”?</span>
            <button
              type="button"
              onClick={() => {
                remove(active.id);
                setConfirm(false);
              }}
              className="text-down hover:underline"
            >
              Yes, delete
            </button>
            <button type="button" onClick={() => setConfirm(false)} className="text-mute hover:text-ink">
              Keep
            </button>
          </>
        ) : (
          <button type="button" onClick={() => setConfirm(true)} className="text-mute hover:text-down">
            Delete list
          </button>
        )}
      </span>
    </div>
  );
}

/** My watch lists: lists I made, each stock with the latest scan result. Kept in this browser. */
export function Watchlist() {
  const { active, toggle } = useWatchlists();
  const symbols = active.symbols;
  const result = useScreener({ ...DEFAULT_QUERY, symbols, pageSize: 100 }, symbols.length > 0);
  const rows = result.data?.rows ?? [];
  const live = useLiveQuotes(rows.map((r) => r.symbol), rows.length > 0);
  const found = new Set(rows.map((r) => r.symbol));
  const missing = result.data ? symbols.filter((s) => !found.has(s)) : [];
  const cols = COLS.filter((c) => c.key !== "watch");

  return (
    <div className="flex min-h-screen flex-col">
      <Nav active="Watchlist" />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Watchlist</h1>
        <Card
          title="My watch lists"
          subtitle={
            result.data?.as_of
              ? `${active.name}: ${symbols.length} stock${symbols.length === 1 ? "" : "s"} · scan of ${fmtDay(result.data.as_of)}`
              : `${active.name}: ${symbols.length} stocks`
          }
          right={<AddStock />}
        >
          <Tabs />
          {symbols.length === 0 ? (
            <Empty>
              This list is empty. Press ☆ next to a stock on the Screener or Stock Analysis page, or
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
                    {cols.map((c) => (
                      <th key={c.key} scope="col" className={`px-3 py-1.5 font-medium ${alignClass(c)}`}>
                        {c.label}
                      </th>
                    ))}
                    <th scope="col" className="px-2 py-1.5 font-medium">
                      <span className="sr-only">Remove</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.instrument_id} className="border-t border-line">
                      {cols.map((c) => (
                        <td key={c.key} className={`px-3 py-1.5 ${alignClass(c)}`}>
                          {c.render(r, live.bySymbol.get(r.symbol))}
                        </td>
                      ))}
                      <td className="px-2 py-1.5 text-right">
                        <button
                          type="button"
                          aria-label={`Remove ${r.symbol} from ${active.name}`}
                          onClick={() => toggle(active.id, r.symbol)}
                          className="text-mute hover:text-down"
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {missing.length > 0 ? (
            <p className="mt-3 text-xs text-mute">Not in the latest scan: {missing.join(", ")}.</p>
          ) : null}
        </Card>
        <p className="text-[11px] text-mute">
          These lists are kept in this browser only. They are not sent anywhere and do not change
          any scan, score, strategy or the ledger. Research lists, not buy signals.
        </p>
      </main>
      <StatusBar />
    </div>
  );
}
