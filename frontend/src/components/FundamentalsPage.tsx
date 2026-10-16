"use client";

// The Fundamentals page (/fundamentals; FUNDAMENTALS_SPECIFICATION §11, FRONTEND_SPECIFICATION
// §17): every universe stock's latest fundamentals, sortable and filterable on the server.
// Period end and publication date are separate columns. A Results / Shareholding switch picks
// the column set; every filter applies in both views. Display only.
import { useState } from "react";
import { useFundamentals } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, tone } from "@/lib/fmt";
import { DEFAULT_FUNDAMENTALS_QUERY, SUPPORTING_NOTE, fmtPp, quarterLabel, statusText, type FundamentalsQuery } from "@/lib/fundamentals";
import type { FundamentalsRow } from "@/lib/schemas";
import { SECTORS } from "@/lib/screener";
import { Nav } from "./Nav";
import { FIELD, Field, Pages } from "./Screener";
import { StatusBar } from "./StatusBar";
import { WatchStar } from "./WatchStar";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const numOrUndef = (v: string): number | undefined => (v === "" ? undefined : Number(v));

function Pct({ r, k, pp }: { r: FundamentalsRow; k: keyof FundamentalsRow & string; pp?: boolean }) {
  const v = r[k] as number | null;
  if (v === null) {
    const why = statusText(r.statuses[k]);
    return (
      <span className="text-mute" title={why}>
        {DASH}
      </span>
    );
  }
  return <span className={tone(v)}>{pp ? fmtPp(v) : fmtPct(v, 1, true)}</span>;
}

/** A change in percentage points with its up/down colour; a dash when the quarter before is missing. */
function Pts({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) {
    return (
      <span className="text-mute" title="The quarter before was not filed">
        {DASH}
      </span>
    );
  }
  return <span className={tone(v)}>{fmtPp(v, 2)}</span>;
}

const pctOrDash = (v: number | null | undefined) => (v === null || v === undefined ? <span className="text-mute">{DASH}</span> : fmtPct(v, 2));

type Col = { key: string; label: string; title?: string; left?: boolean; w?: number; sort?: boolean; render: (r: FundamentalsRow, n: number) => React.ReactNode };

/** The stock columns both views share. */
const COMMON_COLS: Col[] = [
  { key: "no", label: "#", w: 44, render: (_r, n) => <span className="text-mute">{n}</span> },
  { key: "watch", label: "", w: 36, render: (r) => <WatchStar symbol={r.symbol} /> },
  {
    key: "symbol",
    label: "Symbol",
    left: true,
    w: 140,
    sort: true,
    render: (r) => (
      <a href={`/stocks/${encodeURIComponent(r.symbol)}`} title={r.company ?? undefined} className="font-medium text-accent hover:underline">
        {r.symbol}
      </a>
    ),
  },
  {
    key: "sector",
    label: "Sector",
    left: true,
    w: 160,
    render: (r) =>
      r.sector ? (
        <span className="block truncate" title={r.sector}>
          {r.sector}
        </span>
      ) : (
        DASH
      ),
  },
  { key: "rs_rank", label: "RS", w: 52, sort: true, render: (r) => fmtInt(r.rs_rank) },
];

/** The latest quarter's results (F5). */
const RESULT_COLS: Col[] = [
  {
    key: "period_end",
    label: "Quarter",
    title: "Period end of the latest quarter",
    sort: true,
    render: (r) => (
      <span title={fmtDay(r.period_end)}>
        {quarterLabel(r.period_end)}
        {r.estimated ? (
          <span className="ml-1 text-[10px] text-mute" title="Derived from year-to-date figures">
            est.
          </span>
        ) : null}
      </span>
    ),
  },
  {
    key: "available_at",
    label: "Published",
    title: "When the filing was published (first used at the next close if after 15:30 IST)",
    sort: true,
    render: (r) => <span className="text-mute">{r.available_at ? fmtDay(r.available_at.slice(0, 10)) : DASH}</span>,
  },
  { key: "eps_yoy", label: "EPS YoY", sort: true, render: (r) => <Pct r={r} k="eps_yoy" /> },
  { key: "eps_qoq", label: "EPS QoQ", sort: true, render: (r) => <Pct r={r} k="eps_qoq" /> },
  { key: "sales_yoy", label: "Sales YoY", sort: true, render: (r) => <Pct r={r} k="sales_yoy" /> },
  {
    key: "eps_acceleration",
    label: "EPS accel.",
    title: "EPS growth YoY now minus a quarter ago (percentage points)",
    sort: true,
    render: (r) => <Pct r={r} k="eps_acceleration" pp />,
  },
  {
    key: "margin_expansion",
    label: "Margin chg",
    title: "Operating margin now minus a year ago (percentage points)",
    sort: true,
    render: (r) => <Pct r={r} k="margin_expansion" pp />,
  },
  {
    key: "roe",
    label: "ROE",
    title: "Trailing four quarters' profit to owners over average equity (a level, not a change)",
    sort: true,
    render: (r) =>
      r.roe === null ? (
        <span className="text-mute" title={statusText(r.statuses["roe"])}>
          {DASH}
        </span>
      ) : (
        fmtPct(r.roe)
      ),
  },
  {
    key: "debt_to_equity",
    label: "D/E",
    sort: true,
    render: (r) =>
      r.debt_to_equity === null ? (
        <span className="text-mute" title={statusText(r.statuses["debt_to_equity"])}>
          {DASH}
        </span>
      ) : (
        fmtNum(r.debt_to_equity, 2)
      ),
  },
  { key: "ttm_eps", label: "TTM EPS", sort: true, render: (r) => fmtNum(r.ttm_eps, 2) },
  {
    key: "availability",
    label: "Data",
    title: "Metrics available out of 7; stale when the latest quarter is older than 120 days",
    sort: true,
    render: (r) => (
      <span className={r.stale ? "text-warn" : "text-ink"}>
        {Math.round(r.availability * 7)}/7{r.stale ? " ⚠" : ""}
      </span>
    ),
  },
];

/** The latest quarter-end shareholding pattern (F8): holdings in % of shares, changes over one quarter in points. */
const SHARE_COLS: Col[] = [
  {
    key: "shareholding_as_on",
    label: "As on",
    title: "Quarter end of the latest shareholding pattern; ⚠ when older than 120 days",
    render: (r) =>
      r.shareholding_as_on ? (
        <span className={r.shareholding_stale ? "text-warn" : undefined} title={fmtDay(r.shareholding_as_on)}>
          {quarterLabel(r.shareholding_as_on)}
          {r.shareholding_stale ? " ⚠" : ""}
        </span>
      ) : (
        <span className="text-mute" title="No shareholding pattern filed yet">
          {DASH}
        </span>
      ),
  },
  { key: "promoter_pct", label: "Promoter", title: "Promoter and promoter group, % of shares", sort: true, render: (r) => pctOrDash(r.promoter_pct) },
  { key: "promoter_change_1q", label: "Prom. chg", title: "Promoter change over the quarter, percentage points", sort: true, render: (r) => <Pts v={r.promoter_change_1q} /> },
  { key: "fii_pct", label: "FII", title: "Foreign portfolio investors, % of shares", sort: true, render: (r) => pctOrDash(r.fii_pct) },
  { key: "fii_change_1q", label: "FII chg", title: "FII change over the quarter, percentage points", sort: true, render: (r) => <Pts v={r.fii_change_1q} /> },
  { key: "dii_pct", label: "DII", title: "Mutual funds, insurers, banks and other domestic institutions, % of shares", sort: true, render: (r) => pctOrDash(r.dii_pct) },
  { key: "dii_change_1q", label: "DII chg", title: "DII change over the quarter, percentage points", sort: true, render: (r) => <Pts v={r.dii_change_1q} /> },
  {
    key: "pledged_pct",
    label: "Pledge",
    title: "Promoter shares pledged, % of the promoter group's own shares; ▲ when higher than a quarter before",
    sort: true,
    render: (r) =>
      r.pledge_rising ? (
        <span className="text-down" title="Pledge rose over the last quarter">
          {fmtPct(r.pledged_pct ?? null, 2)} ▲
        </span>
      ) : (
        pctOrDash(r.pledged_pct)
      ),
  },
];

export type FundView = "results" | "shareholding";
export const FUND_COLS: Col[] = [...COMMON_COLS, ...RESULT_COLS];
export const SHAREHOLDING_COLS: Col[] = [...COMMON_COLS, ...SHARE_COLS];

function Filters({ q, onChange, onReset }: { q: FundamentalsQuery; onChange: (n: Partial<FundamentalsQuery>) => void; onReset: () => void }) {
  const changed =
    q.q !== "" ||
    q.sector !== undefined ||
    q.minEpsYoy !== undefined ||
    q.minSalesYoy !== undefined ||
    q.minAvailability !== undefined ||
    q.accelerating ||
    q.currentOnly ||
    q.minPromoterPct !== undefined ||
    q.maxPledgedPct !== undefined ||
    q.fiiBuying ||
    q.pledgeRising;
  return (
    <Card
      title="Filters"
      label="Filters"
      right={
        <div className="flex items-center gap-4 text-xs text-ink">
          <label className="flex cursor-pointer items-center gap-1.5">
            <input type="checkbox" checked={q.accelerating} onChange={(e) => onChange({ accelerating: e.target.checked })} />
            EPS accelerating
          </label>
          <label className="flex cursor-pointer items-center gap-1.5">
            <input type="checkbox" checked={q.currentOnly} onChange={(e) => onChange({ currentOnly: e.target.checked })} />
            Current data only
          </label>
          <label className="flex cursor-pointer items-center gap-1.5" title="FII holding up over the latest quarter">
            <input type="checkbox" checked={q.fiiBuying} onChange={(e) => onChange({ fiiBuying: e.target.checked })} />
            FII buying
          </label>
          <label className="flex cursor-pointer items-center gap-1.5" title="Promoters pledged more of their shares than a quarter before">
            <input type="checkbox" checked={q.pledgeRising} onChange={(e) => onChange({ pledgeRising: e.target.checked })} />
            Pledge rising
          </label>
        </div>
      }
    >
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_repeat(5,minmax(0,1fr))_auto]">
        <Field id="fun-q" label="Symbol or company" className="col-span-2 md:col-span-1">
          <input id="fun-q" type="search" value={q.q} maxLength={40} onChange={(e) => onChange({ q: e.target.value })} className={FIELD} placeholder="e.g. RELIANCE" />
        </Field>
        <Field id="fun-sector" label="Sector" className="col-span-2 md:col-span-1">
          <select id="fun-sector" value={q.sector ?? ""} onChange={(e) => onChange({ sector: e.target.value || undefined })} className={FIELD}>
            <option value="">Any</option>
            {SECTORS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field id="fun-eps" label="Min EPS growth YoY %">
          <input
            id="fun-eps"
            type="number"
            step={5}
            value={q.minEpsYoy ?? ""}
            onChange={(e) => onChange({ minEpsYoy: numOrUndef(e.target.value) })}
            className={FIELD}
            placeholder="e.g. 25"
          />
        </Field>
        <Field id="fun-sales" label="Min sales growth YoY %">
          <input
            id="fun-sales"
            type="number"
            step={5}
            value={q.minSalesYoy ?? ""}
            onChange={(e) => onChange({ minSalesYoy: numOrUndef(e.target.value) })}
            className={FIELD}
            placeholder="e.g. 20"
          />
        </Field>
        <Field id="fun-avail" label="Data available">
          <select id="fun-avail" value={q.minAvailability ?? ""} onChange={(e) => onChange({ minAvailability: numOrUndef(e.target.value) })} className={FIELD}>
            <option value="">Any</option>
            <option value={4 / 7}>4 of 7 or more</option>
            <option value={6 / 7}>6 of 7 or more</option>
            <option value={1}>All 7</option>
          </select>
        </Field>
        <Field id="fun-promoter" label="Min promoter %">
          <input
            id="fun-promoter"
            type="number"
            min={0}
            max={100}
            step={5}
            value={q.minPromoterPct ?? ""}
            onChange={(e) => onChange({ minPromoterPct: numOrUndef(e.target.value) })}
            className={FIELD}
            placeholder="e.g. 50"
          />
        </Field>
        <Field id="fun-pledge" label="Max pledge %">
          <input
            id="fun-pledge"
            type="number"
            min={0}
            max={100}
            step={1}
            value={q.maxPledgedPct ?? ""}
            onChange={(e) => onChange({ maxPledgedPct: numOrUndef(e.target.value) })}
            className={FIELD}
            placeholder="e.g. 5"
          />
        </Field>
        <div className="col-span-2 flex items-end md:col-span-1">
          <button
            type="button"
            disabled={!changed}
            onClick={onReset}
            className="w-full whitespace-nowrap rounded border border-line px-2 py-1.5 text-xs text-mute enabled:hover:text-ink disabled:opacity-40"
          >
            Reset filters
          </button>
        </div>
      </div>
    </Card>
  );
}

export function FundamentalsPage() {
  const [query, setQuery] = useState<FundamentalsQuery>(DEFAULT_FUNDAMENTALS_QUERY);
  const result = useFundamentals(query);
  const data = result.data;
  const change = (next: Partial<FundamentalsQuery>) => setQuery((q) => ({ ...q, ...next, page: 1 }));
  const [view, setView] = useState<FundView>("results");
  const cols = view === "results" ? FUND_COLS : SHAREHOLDING_COLS;
  return (
    <div className="flex min-h-screen flex-col">
      <Nav active="Fundamentals" />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Fundamentals</h1>
        <Filters q={query} onChange={change} onReset={() => setQuery(DEFAULT_FUNDAMENTALS_QUERY)} />
        <Card
          title={view === "results" ? "Latest quarter of every universe stock" : "Latest shareholding pattern of every universe stock"}
          subtitle={
            data
              ? `As of the close of ${fmtDay(data.as_of)}: ${fmtInt(data.covered)} of ${fmtInt(data.universe)} universe stocks have results filed. ${SUPPORTING_NOTE}`
              : SUPPORTING_NOTE
          }
          right={
            <div className="flex items-center gap-3">
              <div role="group" aria-label="Columns" className="flex overflow-hidden rounded border border-line text-xs">
                {(["results", "shareholding"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    aria-pressed={view === v}
                    onClick={() => setView(v)}
                    className={`px-2.5 py-1 ${view === v ? "bg-accent/20 text-ink" : "text-mute hover:text-ink"}`}
                  >
                    {v === "results" ? "Results" : "Shareholding"}
                  </button>
                ))}
              </div>
              {data ? <span className="text-xs text-mute">{`${fmtInt(data.total)} match`}</span> : null}
            </div>
          }
        >
          {result.isError ? (
            <ErrorBox error={result.error} />
          ) : !data ? (
            <Loading what="fundamentals" />
          ) : data.rows.length === 0 ? (
            <Empty>No stock matches these filters.</Empty>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className={`w-full ${view === "results" ? "min-w-[1200px]" : "min-w-[960px]"} table-fixed text-xs tabular-nums`}>
                  <colgroup>
                    {cols.map((c) => (
                      <col key={c.key} style={c.w ? { width: c.w } : undefined} />
                    ))}
                  </colgroup>
                  <thead className="text-[11px] uppercase text-mute">
                    <tr>
                      {cols.map((c) => {
                        const on = query.sort === c.key;
                        return (
                          <th
                            key={c.key}
                            scope="col"
                            title={c.title}
                            className={`px-2 py-1.5 font-medium ${c.left ? "text-left" : "text-center"}`}
                            aria-sort={on ? (query.direction === "asc" ? "ascending" : "descending") : "none"}
                          >
                            {c.sort ? (
                              <button
                                type="button"
                                onClick={() =>
                                  setQuery((q) => ({
                                    ...q,
                                    sort: c.key,
                                    direction: q.sort === c.key && q.direction === "desc" ? "asc" : "desc",
                                    page: 1,
                                  }))
                                }
                                className="uppercase hover:text-ink"
                              >
                                {c.label}
                                {on ? (query.direction === "asc" ? " ▲" : " ▼") : ""}
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
                    {data.rows.map((r, i) => (
                      <tr key={r.instrument_id} className="border-t border-line">
                        {cols.map((c) => (
                          <td key={c.key} className={`px-2 py-1.5 ${c.left ? "text-left" : "text-center"}`}>
                            {c.render(r, (data.page - 1) * data.page_size + i + 1)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pages page={data.page} total={data.total} size={data.page_size} onPage={(p) => setQuery((q) => ({ ...q, page: p }))} />
              <p className="mt-2 text-[11px] text-mute">
                {view === "results"
                  ? "A missing value is a dash, never 0; hover it for the reason. A stock with no year-ago quarter, or a loss a year ago, has no growth %."
                  : "Quarter-end filings to NSE, latest revision. Changes are percentage points over one quarter; a dash when that quarter was not filed. Pledge is % of the promoter group's own shares (pledges only from June 2025; pledged or encumbered before, so no change is shown across that switch)."}
              </p>
            </>
          )}
        </Card>
      </main>
      <StatusBar />
    </div>
  );
}
