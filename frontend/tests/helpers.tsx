// Test helpers: a fetch that answers from the committed API samples (tests/fixtures/api, kept
// equal to the real API by tests/api/test_contract_samples.py), and a render with a query client.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import activity from "./fixtures/api/activity.json";
import bars from "./fixtures/api/bars.json";
import liveIndices from "./fixtures/api/live_indices.json";
import liveQuotes from "./fixtures/api/live_quotes.json";
import liveStatus from "./fixtures/api/live_status.json";
import history from "./fixtures/api/stock_history.json";
import marketHealth from "./fixtures/api/market_health.json";
import market from "./fixtures/api/market.json";
import overlap from "./fixtures/api/overlap.json";
import paper from "./fixtures/api/paper.json";
import screener from "./fixtures/api/screener.json";
import search from "./fixtures/api/search.json";
import setupsFlat from "./fixtures/api/setups_flat_base.json";
import setupsVcp from "./fixtures/api/setups_vcp.json";
import status from "./fixtures/api/status.json";
import stockSetups from "./fixtures/api/stock_setups.json";
import strategies from "./fixtures/api/strategies.json";
import summary from "./fixtures/api/summary.json";

export const fx = {
  activity, bars, history, liveIndices, liveQuotes, liveStatus, market, marketHealth, overlap, paper, search, screener, setupsFlat, setupsVcp, status,
  stockSetups, strategies, summary,
}; // fmt: skip

/** What the API would answer for a path ("setups?strategy=vcp"); undefined: no such sample. */
export function sample(rel: string): unknown {
  const [path = "", qs = ""] = rel.split("?");
  const p = new URLSearchParams(qs);
  switch (path) {
    case "status": return fx.status;
    case "strategies": return fx.strategies;
    case "summary": return fx.summary;
    case "market": return fx.market;
    case "market/health": return fx.marketHealth;
    case "activity": return fx.activity;
    case "paper": return fx.paper;
    case "search": return fx.search;
    case "screener": return fx.screener;
    case "live/indices": return fx.liveIndices;
    case "live/status": return fx.liveStatus;
    case "live/quotes": {
      const want = (p.get("symbols") ?? "").split(",").filter(Boolean);
      return { ...fx.liveQuotes, quotes: fx.liveQuotes.quotes.filter((q) => want.includes(q.symbol)) };
    }
    case "setups/overlap": return fx.overlap;
    case "setups": {
      const id = p.get("strategy") ?? "vcp";
      const base = id === "vcp" ? fx.setupsVcp : id === "flat_base" ? fx.setupsFlat : { ...fx.setupsVcp, rows: [] };
      const rows = base.rows.filter((r) => !p.get("status") || r.status === p.get("status"));
      return { ...base, strategy_id: id, rows: id === "vcp" || id === "flat_base" ? rows : [] };
    }
  }
  const m = /^stocks\/([^/]+)\/(bars|setups|history)$/.exec(path);
  if (m) {
    const symbol = decodeURIComponent(m[1] ?? "");
    if (m[2] === "bars") return { ...fx.bars, symbol };
    if (m[2] === "history") return { ...fx.history, events: symbol === "BETA" ? fx.history.events : [] };
    // ALPHA has setups in three strategies; any other symbol only the VCP one
    const setups = symbol === "ALPHA" ? fx.stockSetups.setups : fx.stockSetups.setups.slice(0, 1);
    return { ...fx.stockSetups, symbol, setups };
  }
  return undefined;
}

export type Overrides = Record<string, unknown>;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** Replace fetch. ``over`` maps a path (with or without its query) to a body, or to an Error
 *  (the network is down); returns the list of requested paths. */
export function mockApi(over: Overrides = {}): string[] {
  const calls: string[] = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const rel = String(input).replace(/^\/api\/v1\//, "");
    calls.push(rel);
    const hit = over[rel] ?? over[rel.split("?")[0] ?? ""];
    if (hit instanceof Error) throw hit;
    const body = hit !== undefined ? hit : sample(rel);
    return body === undefined ? json({ detail: `no sample for ${rel}` }, 404) : json(body);
  }) as unknown as typeof fetch;
  return calls;
}

export function renderApp(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
