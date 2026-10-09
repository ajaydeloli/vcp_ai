"use client";

import { useMemo, useState } from "react";
import { useIpos } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, tone } from "@/lib/fmt";
import type { IpoRow } from "@/lib/schemas";
import { Page } from "./Page";
import { Card, Empty, ErrorBox, Loading, TINT } from "./ui";

const PAGE_SIZE = 15;

const SORTS: [string, string, (r: IpoRow) => number | null][] = [
  ["listed", "Newest listed", (r) => -r.bars],
  ["since", "Since listing", (r) => r.since_listing_pct],
  ["firsthigh", "Close to first-day high", (r) => r.vs_first_day_high_pct],
  ["high", "Close to high since listing", (r) => r.from_high_pct],
  ["value", "Traded value", (r) => r.avg_traded_value],
];

const TH = "px-2 py-2 text-center font-medium";
const TD = "px-2 py-2 text-center tabular-nums";

/** Stocks with fewer than a full year of bars. Outside the scan universe, so display only. */
export function RecentIpos() {
  const ipos = useIpos();
  const [minBars, setMinBars] = useState("");
  const [text, setText] = useState("");
  const [sort, setSort] = useState("listed");
  const [page, setPage] = useState(1);

  const rows = useMemo(() => {
    const min = Number(minBars) || 1;
    const t = text.trim().toLowerCase();
    const by = SORTS.find((s) => s[0] === sort)?.[2] ?? SORTS[0]![2];
    return (ipos.data?.rows ?? [])
      .filter((r) => r.bars >= min && (!t || r.symbol.toLowerCase().includes(t) || (r.company ?? "").toLowerCase().includes(t)))
      .sort((a, b) => {
        const x = by(a);
        const y = by(b);
        if (x === null) return y === null ? 0 : 1; // a missing value goes last
        if (y === null) return -1;
        return y - x;
      });
  }, [ipos.data, minBars, text, sort]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const at = Math.min(page, pages);
  const shown = rows.slice((at - 1) * PAGE_SIZE, at * PAGE_SIZE);
  const input = "rounded border border-line bg-panel2 px-2 py-1 text-xs text-ink focus:border-accent focus:outline-none";

  return (
    <Page active="Recent IPOs" title="Recent IPOs">
      <Card
        title="Recent IPOs"
        subtitle={
          ipos.data
            ? `Main-board listings with fewer than ${ipos.data.full_history_bars} trading days of prices. Prices to ${fmtDay(ipos.data.as_of)}. Display only: not part of the scan universe, the scores, the strategies or the paper trades.`
            : undefined
        }
        box={TINT.cyan}
        right={
          <span className="text-xs text-mute">
            {rows.length} listing{rows.length === 1 ? "" : "s"}
          </span>
        }
      >
        <div className="mb-3 flex flex-wrap items-end gap-x-4 gap-y-2 text-xs">
          <label className="flex flex-col gap-1 text-mute">
            Symbol or company
            <input
              type="search"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setPage(1);
              }}
              className={`${input} w-40`}
            />
          </label>
          <label className="flex flex-col gap-1 text-mute">
            Min bars
            <input
              type="number"
              min={1}
              max={252}
              placeholder="1"
              value={minBars}
              onChange={(e) => {
                setMinBars(e.target.value);
                setPage(1);
              }}
              className={`${input} w-20`}
            />
          </label>
          <label className="flex flex-col gap-1 text-mute">
            Sort by
            <select
              value={sort}
              onChange={(e) => {
                setSort(e.target.value);
                setPage(1);
              }}
              className={`${input} w-52`}
            >
              {SORTS.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {ipos.isError ? (
          <ErrorBox error={ipos.error} />
        ) : ipos.isPending ? (
          <Loading what="recent listings" />
        ) : shown.length === 0 ? (
          <Empty>No listing matches.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full table-fixed text-xs">
              <thead className="text-[11px] uppercase text-mute">
                <tr>
                  <th className={`${TH} w-10`}>#</th>
                  <th className="w-44 px-2 py-2 text-left font-medium">Symbol</th>
                  <th className={TH}>Listed</th>
                  <th className={TH}>Bars</th>
                  <th className={TH}>IPO close</th>
                  <th className={TH}>Close</th>
                  <th className={TH}>Change</th>
                  <th className={TH}>Since listing</th>
                  <th className={TH}>vs first-day high</th>
                  <th className={TH}>vs high since</th>
                  <th className={TH}>vs 20-day</th>
                  <th className={TH}>vs 50-day</th>
                  <th className={TH}>10-day range</th>
                  <th className={TH}>Traded value</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r, i) => (
                  <tr key={r.instrument_id} className="border-t border-line/60">
                    <td className={`${TD} text-mute`}>{(at - 1) * PAGE_SIZE + i + 1}</td>
                    <td className="px-2 py-2 text-left">
                      <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium text-ink hover:text-accent">
                        {r.symbol}
                      </a>
                      <span className="block truncate text-[11px] text-mute">{r.company ?? DASH}</span>
                    </td>
                    <td className={TD}>{fmtDay(r.listing_date)}</td>
                    <td className={TD}>{fmtInt(r.bars)}</td>
                    <td className={TD}>{fmtPrice(r.ipo_close)}</td>
                    <td className={TD}>{fmtPrice(r.close)}</td>
                    <td className={`${TD} ${tone(r.change_pct)}`}>{fmtPct(r.change_pct, 2, true)}</td>
                    <td className={`${TD} ${tone(r.since_listing_pct)}`}>{fmtPct(r.since_listing_pct, 1, true)}</td>
                    <td className={`${TD} ${tone(r.vs_first_day_high_pct)}`}>{fmtPct(r.vs_first_day_high_pct, 1, true)}</td>
                    <td className={`${TD} ${tone(r.from_high_pct)}`}>{fmtPct(r.from_high_pct, 1, true)}</td>
                    <td className={`${TD} ${tone(r.vs_sma20_pct)}`}>{fmtPct(r.vs_sma20_pct, 1, true)}</td>
                    <td className={`${TD} ${tone(r.vs_sma50_pct)}`}>{fmtPct(r.vs_sma50_pct, 1, true)}</td>
                    <td className={TD}>{fmtPct(r.range_pct, 1)}</td>
                    <td className={TD} title="Mean close × volume over the last 20 bars, in crore rupees">
                      {r.avg_traded_value === null ? DASH : `${fmtNum(r.avg_traded_value / 1e7, 1)} Cr`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 ? (
          <nav aria-label="Pages" className="mt-3 flex items-center justify-end gap-2 text-xs">
            <button type="button" disabled={at <= 1} onClick={() => setPage(at - 1)} className="rounded border border-line px-2 py-1 disabled:opacity-40">
              Previous
            </button>
            <span className="text-mute">
              Page {at} of {pages}
            </span>
            <button type="button" disabled={at >= pages} onClick={() => setPage(at + 1)} className="rounded border border-line px-2 py-1 disabled:opacity-40">
              Next
            </button>
          </nav>
        ) : null}
        <p className="mt-3 text-[11px] text-mute">
          A measure that needs more bars than the stock has (the 20-day and 50-day averages, the 10-day range) shows {DASH}, not 0. Prices are not
          live; the stored close is used. A filter for research, not buy signals.
        </p>
      </Card>
    </Page>
  );
}
