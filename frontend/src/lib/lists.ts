import type { SetupRow } from "./schemas";

/** The lists of the watch list tabs and the KPI tiles, all cut from one ranked VCP list. */
export type VcpList = { id: string; label: string; test: (r: SetupRow) => boolean };

export const VCP_LISTS: VcpList[] = [
  { id: "top", label: "Top setups", test: () => true },
  { id: "aplus", label: "A+ VCP", test: (r) => r.classification === "A_PLUS_VCP" },
  { id: "vcp", label: "VCP", test: (r) => r.classification === "VCP" },
  { id: "vcp_like", label: "VCP like", test: (r) => r.classification === "VCP_LIKE" },
  { id: "forming", label: "Forming", test: (r) => r.status === "FORMING" },
  {
    id: "watch",
    label: "Breakout watch",
    test: (r) => r.status === "PIVOT_READY" || r.status === "BREAKOUT",
  },
];

export const countList = (rows: SetupRow[], id: string): number =>
  rows.filter(VCP_LISTS.find((l) => l.id === id)?.test ?? (() => false)).length;

export const PAGE_SIZE = 10;

/** Page numbers to show: first, last, and two either side of the current page ("…" between). */
export function pageNumbers(page: number, pages: number): (number | "…")[] {
  const keep = new Set<number>([0, pages - 1, page - 2, page - 1, page, page + 1, page + 2]);
  const sorted = [...keep].filter((p) => p >= 0 && p < pages).sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  sorted.forEach((p, i) => {
    const prev = sorted[i - 1];
    if (prev !== undefined && p - prev > 1) out.push("…");
    out.push(p);
  });
  return out;
}
