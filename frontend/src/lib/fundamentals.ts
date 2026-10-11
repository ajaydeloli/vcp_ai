// Fundamentals (F5): query state of the Fundamentals page and display helpers shared by the
// page, the stock card and the Screener. Display only: fundamentals never change a setup score
// or the paper ledger (FUNDAMENTALS_SPECIFICATION §2). A missing value is a dash, never 0.
import { DASH } from "./fmt";

export type FundamentalsQuery = {
  q: string;
  sector?: string;
  minEpsYoy?: number;
  minSalesYoy?: number;
  /** 0..1, share of the seven metrics present */
  minAvailability?: number;
  accelerating: boolean;
  currentOnly: boolean;
  sort: string;
  direction: "asc" | "desc";
  page: number;
  pageSize: number;
};

export const PAGE_SIZE_FUNDAMENTALS = 25;

export const DEFAULT_FUNDAMENTALS_QUERY: FundamentalsQuery = {
  q: "",
  accelerating: false,
  currentOnly: false,
  sort: "eps_yoy",
  direction: "desc",
  page: 1,
  pageSize: PAGE_SIZE_FUNDAMENTALS,
};

export const SUPPORTING_NOTE =
  "Fundamentals are supporting information only: they do not change the setup score or the paper ledger.";

/** Rupees -> "₹3,11,850 cr" (Indian grouping); a dash when missing. */
export const fmtCrore = (rupees: number | null | undefined): string => {
  if (rupees === null || rupees === undefined) return DASH;
  const crore = rupees / 1e7;
  const digits = Math.abs(crore) >= 100 ? 0 : 2;
  return `₹${crore.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })} cr`;
};

/** Percentage points with a sign: "+3.2 pp". */
export const fmtPp = (v: number | null | undefined): string =>
  v === null || v === undefined ? DASH : `${v > 0 ? "+" : ""}${v.toFixed(1)} pp`;

/** Why a metric is null, in words (statuses of FUNDAMENTALS_SPECIFICATION §8). */
export const STATUS_TEXT: Record<string, string> = {
  MISSING: "Not enough quarters filed yet",
  NEGATIVE_BASE: "Earlier value zero or negative (a turnaround has no %)",
  BASIS_CHANGE: "Earlier quarter filed on a different basis (standalone / consolidated)",
  NOT_APPLICABLE: "Not applicable (banks and financials)",
};

export const statusText = (status: string | undefined): string | undefined =>
  status && status !== "AVAILABLE" ? (STATUS_TEXT[status] ?? status) : undefined;

/** "2026-06-30" -> "Jun 26" (quarter column label). */
export const quarterLabel = (periodEnd: string): string => {
  const [y, m] = periodEnd.split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[Number(m) - 1] ?? m} ${y?.slice(2) ?? ""}`;
};

export const GATE_TEXT: Record<string, string> = {
  NO_DATA: "no results filed",
  INSUFFICIENT_DATA: "fewer than half of the metrics available",
  NEGATIVE_TTM_EPS: "negative trailing-twelve-month EPS",
};

export const gateText = (reason: string | null): string =>
  (reason ?? "")
    .split(";")
    .filter(Boolean)
    .map((r) => GATE_TEXT[r] ?? r)
    .join(", ");
