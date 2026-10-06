// Typed, validated access to the read-only API (GET only). The browser calls this server's
// /api/v1/* path, which next.config.mjs forwards to `vcp api serve`.
import { useQuery } from "@tanstack/react-query";
import type { z } from "zod";
import * as s from "./schemas";

export class ApiError extends Error {
  readonly status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

export async function getJson<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/v1/${path}`, { headers: { accept: "application/json" } });
  } catch {
    throw new ApiError("The API is not reachable. Start it with: vcp api serve");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail || `HTTP ${res.status}`, res.status);
  }
  const parsed = schema.safeParse(await res.json());
  if (!parsed.success) {
    throw new ApiError(`Unexpected response from /${path}: ${parsed.error.issues[0]?.message}`);
  }
  return parsed.data;
}

type Params = Record<string, string | number | boolean | undefined | null>;

const query = (params: Params): string => {
  const e = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return e.length ? `?${e.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&")}` : "";
};

export const paths = {
  status: () => "status",
  strategies: () => "strategies",
  summary: () => "summary",
  market: (days: number) => `market${query({ days })}`,
  setups: (strategy: string, status?: string) => `setups${query({ strategy, status })}`,
  overlap: () => "setups/overlap",
  bars: (symbol: string, days: number) =>
    `stocks/${encodeURIComponent(symbol)}/bars${query({ days })}`,
  stockSetups: (symbol: string) => `stocks/${encodeURIComponent(symbol)}/setups`,
  activity: (days: number) => `activity${query({ days })}`,
  paper: () => "paper",
  search: (text: string) => `search${query({ q: text, limit: 8 })}`,
};

const REFRESH_MS = 5 * 60 * 1000; // the data changes once a day; this only notices a new copy

// retry is set on the query client (app/providers.tsx), so tests can switch it off
const opts = { staleTime: 60_000, refetchInterval: REFRESH_MS } as const;

export const useStatus = () =>
  useQuery({ queryKey: ["status"], queryFn: () => getJson(paths.status(), s.status), ...opts });
export const useStrategies = () =>
  useQuery({
    queryKey: ["strategies"],
    queryFn: () => getJson(paths.strategies(), s.strategies),
    ...opts,
  });
export const useSummary = () =>
  useQuery({ queryKey: ["summary"], queryFn: () => getJson(paths.summary(), s.summary), ...opts });
export const useMarket = (days = 250) =>
  useQuery({
    queryKey: ["market", days],
    queryFn: () => getJson(paths.market(days), s.market),
    ...opts,
  });
export const useSetups = (strategy: string, status?: string, enabled = true) =>
  useQuery({
    queryKey: ["setups", strategy, status ?? null],
    queryFn: () => getJson(paths.setups(strategy, status), s.setups),
    enabled,
    ...opts,
  });
export const useOverlap = (enabled = true) =>
  useQuery({
    queryKey: ["overlap"],
    queryFn: () => getJson(paths.overlap(), s.overlap),
    enabled,
    ...opts,
  });
export const useBars = (symbol: string | null, days = 400) =>
  useQuery({
    queryKey: ["bars", symbol, days],
    queryFn: () => getJson(paths.bars(symbol ?? "", days), s.bars),
    enabled: symbol !== null,
    ...opts,
  });
export const useStockSetups = (symbol: string | null) =>
  useQuery({
    queryKey: ["stockSetups", symbol],
    queryFn: () => getJson(paths.stockSetups(symbol ?? ""), s.stockSetups),
    enabled: symbol !== null,
    ...opts,
  });
export const useActivity = (days = 7) =>
  useQuery({
    queryKey: ["activity", days],
    queryFn: () => getJson(paths.activity(days), s.activity),
    ...opts,
  });
export const usePaper = () =>
  useQuery({ queryKey: ["paper"], queryFn: () => getJson(paths.paper(), s.paper), ...opts });
export const useSearch = (text: string) =>
  useQuery({
    queryKey: ["search", text],
    queryFn: () => getJson(paths.search(text), s.search),
    enabled: text.trim().length > 0,
    staleTime: 60_000,
  });
