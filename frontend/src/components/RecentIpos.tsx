"use client";

import { useMemo, useState } from "react";
import { useIpos } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, tone } from "@/lib/fmt";
import type { IpoRow } from "@/lib/schemas";
import { Page } from "./Page";
import { Field, FIELD, Pages } from "./Screener";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const PAGE_SIZE = 15;

type Col = {
  key: string;
  label: string;
  w?: number;
  align?: "left";
  /** the value to sort by; no value: the column is not sortable */
  sort?: (r: IpoRow) => number | string | null;
  render: (r: IpoRow, n: number) => React.ReactNode;
};

function Pct({ v, digits = 1, sign = true }: { v: number | null; digits?: number; sign?: boolean }) {
  return <span className={sign ? tone(v) : undefined}>{fmtPct(v, digits, sign)}</span>;
}

const COLS: Col[] = [
  { key: "no", label: "#", w: 50, render: (_r, n) => <span className="tabular-nums text-mute">{n}</span> },
  {
    key: "symbol",
    label: "Symbol",
    w: 150,
    align: "left",
    sort: (r) => r.symbol,
    render: (r) => (
      <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium text-accent hover:underline">
        {r.symbol}
      </a>
    ),
  },
  { key: "listed", label: "Listed", sort: (r) => r.listing_date, render: (r) => fmtDay(r.listing_date) },
  { key: "bars", label: "Bars", sort: (r) => r.bars, render: (r) => fmtInt(r.bars) },
  { key: "ipo_close", label: "IPO close", render: (r) => fmtPrice(r.ipo_close) },
  { key: "close", label: "Close", render: (r) => fmtPrice(r.close) },
  { key: "change", label: "Change", sort: (r) => r.change_pct, render: (r) => <Pct v={r.change_pct} digits={2} /> },
  { key: "since", label: "Since listing", sort: (r) => r.since_listing_pct, render: (r) => <Pct v={r.since_listing_pct} /> },
  { key: "first", label: "vs first-day high", sort: (r) => r.vs_first_day_high_pct, render: (r) => <Pct v={r.vs_first_day_high_pct} /> },
  { key: "high", label: "vs high since", sort: (r) => r.from_high_pct, render: (r) => <Pct v={r.from_high_pct} /> },
  { key: "sma20", label: "vs 20-day", sort: (r) => r.vs_sma20_pct, render: (r) => <Pct v={r.vs_sma20_pct} /> },
  { key: "sma50", label: "vs 50-day", sort: (r) => r.vs_sma50_pct, render: (r) => <Pct v={r.vs_sma50_pct} /> },
  { key: "range", label: "10-day range", sort: (r) => r.range_pct, render: (r) => <Pct v={r.range_pct} digits={1} sign={false} /> },
  {
    key: "value",
    label: "Traded value",
    sort: (r) => r.avg_traded_value,
    render: (r) => (
      <span title="Mean close × volume over the last 20 bars, in crore rupees">
        {r.avg_traded_value === null ? DASH : `${fmtNum(r.avg_traded_value / 1e7, 1)} Cr`}
      </span>
    ),
  },
];

const align = (c: Pick<Col, "align">): string => (c.align === "left" ? "text-left" : "text-center");

/** Stocks with fewer than a full year of bars. Outside the scan universe, so display only. */
export function RecentIpos() {
  const ipos = useIpos();
  const [text, setText] = useState("");
  const [minBars, setMinBars] = useState("");
  const [sort, setSort] = useState("listed");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);

  const all = useMemo(() => ipos.data?.rows ?? [], [ipos.data]);
  const rows = useMemo(() => {
    const min = Number(minBars) || 1;
    const t = text.trim().toLowerCase();
    const by = COLS.find((c) => c.key === sort)?.sort ?? ((r: IpoRow) => r.listing_date);
    const sign = dir === "asc" ? 1 : -1;
    return all
      .filter((r) => r.bars >= min && (!t || r.symbol.toLowerCase().includes(t) || (r.company ?? "").toLowerCase().includes(t)))
      .sort((a, b) => {
        const x = by(a);
        const y = by(b);
        if (x === null) return y === null ? 0 : 1; // a missing value goes last either way
        if (y === null) return -1;
        return x < y ? -sign : x > y ? sign : 0;
      });
  }, [all, text, minBars, sort, dir]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const at = Math.min(page, pages);
  const shown = rows.slice((at - 1) * PAGE_SIZE, at * PAGE_SIZE);
  const changed = text !== "" || minBars !== "";

  return (
    <Page active="Recent IPOs" title="Recent IPOs">
      <Card title="Filters" label="Filters">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto] xl:max-w-3xl">
          <Field id="ipo-q" label="Symbol or company" className="col-span-2 xl:col-span-1">
            <input
              id="ipo-q"
              type="search"
              value={text}
              maxLength={40}
              onChange={(e) => {
                setText(e.target.value);
                setPage(1);
              }}
              className={FIELD}
              placeholder="e.g. HEROMOTORS"
            />
          </Field>
          <Field id="ipo-bars" label="Min bars">
            <input
              id="ipo-bars"
              type="number"
              min={1}
              max={252}
              placeholder="1"
              aria-label="Min bars"
              value={minBars}
              onChange={(e) => {
                setMinBars(e.target.value);
                setPage(1);
              }}
              className={FIELD}
            />
          </Field>
          <div className="col-span-2 flex items-end md:col-span-1">
            <button
              type="button"
              disabled={!changed}
              onClick={() => {
                setText("");
                setMinBars("");
                setPage(1);
              }}
              className="w-full whitespace-nowrap rounded border border-line px-2 py-1.5 text-xs text-mute enabled:hover:text-ink disabled:opacity-40"
            >
              Reset filters
            </button>
          </div>
        </div>
      </Card>

      <Card
        title="Results"
        subtitle="Outside the scan universe. Display only."
        right={
          ipos.data ? (
            <span className="text-xs text-mute">{`${fmtInt(rows.length)} of ${fmtInt(all.length)} listings match`}</span>
          ) : undefined
        }
      >
        {ipos.isError ? (
          <ErrorBox error={ipos.error} />
        ) : ipos.isPending ? (
          <Loading what="recent listings" />
        ) : rows.length === 0 ? (
          <Empty>No listing matches these filters.</Empty>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1100px] table-fixed text-left text-xs">
                <colgroup>
                  {COLS.map((c) => (
                    <col key={c.key} style={c.w ? { width: c.w } : undefined} />
                  ))}
                </colgroup>
                <thead className="text-[11px] uppercase text-mute">
                  <tr>
                    {COLS.map((c) => {
                      const on = sort === c.key;
                      return (
                        <th
                          key={c.key}
                          scope="col"
                          className={`px-3 py-1.5 font-medium ${align(c)}`}
                          aria-sort={on ? (dir === "asc" ? "ascending" : "descending") : "none"}
                        >
                          {c.sort ? (
                            <button
                              type="button"
                              onClick={() => {
                                setDir(on && dir === "desc" ? "asc" : "desc");
                                setSort(c.key);
                                setPage(1);
                              }}
                              className="uppercase hover:text-ink"
                            >
                              {c.label}
                              {on ? (dir === "asc" ? " ▲" : " ▼") : ""}
                            </button>
                          ) : (
                            c.label
                          )}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r, i) => (
                    <tr key={r.instrument_id} className="border-t border-line">
                      {COLS.map((c) => (
                        <td key={c.key} className={`px-3 py-1.5 ${align(c)}`}>
                          {c.render(r, (at - 1) * PAGE_SIZE + i + 1)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pages page={at} total={rows.length} size={PAGE_SIZE} onPage={setPage} />
          </>
        )}
      </Card>
    </Page>
  );
}
