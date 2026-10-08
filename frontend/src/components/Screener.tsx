"use client";

import { useState } from "react";
import { useLiveQuotes, useScreener, useStrategies } from "@/lib/api";
import { DASH, fmtInt, fmtNum, fmtPct, fmtPrice, strategyLabel, tone } from "@/lib/fmt";
import { pageNumbers } from "@/lib/lists";
import { DEFAULT_QUERY, STAGES, type ScreenerQuery } from "@/lib/screener";
import type { LiveQuote, ScreenerRow } from "@/lib/schemas";
import { LiveCell, LiveNotice } from "./Live";
import { Nav } from "./Nav";
import { WatchStar } from "./WatchStar";
import { StatusBar } from "./StatusBar";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";

const field = "w-full rounded border border-line bg-panel2 px-2 py-1.5 text-xs text-ink";
const stageLabel = (s: string): string => s.replace("STAGE_", "Stage ").replace("TRANSITION", "Transition");

export type Col = {
  key: string;
  label: string;
  align?: "right" | "center";
  /** column width in px; the table is fixed-layout so the columns are spread evenly */
  w: number;
  render: (r: ScreenerRow, live?: LiveQuote) => React.ReactNode;
};

const yesNo = (v: boolean | null) =>
  v === null ? <span className="text-mute">{DASH}</span> : v ? <span className="text-up">Yes</span> : <span className="text-mute">No</span>;

export const alignClass = (c: Pick<Col, "align">): string =>
  c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "";

export const COLS: Col[] = [
  {
    key: "watch",
    w: 40,
    label: "",
    render: (r) => <WatchStar symbol={r.symbol} />,
  },
  {
    key: "symbol",
    w: 130,
    label: "Symbol",
    render: (r) => (
      <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium text-accent hover:underline">
        {r.symbol}
      </a>
    ),
  },
  { key: "stage", w: 90, label: "Stage", render: (r) => (r.stage ? stageLabel(r.stage) : DASH) },
  {
    key: "conditions_passed",
    w: 100,
    label: "Conditions",
    align: "center", render: (r) =>
      r.conditions_passed === null ? DASH : `${r.conditions_passed}/${r.conditions_total ?? DASH}`,
  },
  { key: "near_high", w: 100, label: "Near high", align: "center", render: (r) => yesNo(r.near_52w_high) },
  { key: "rs_rank", w: 70, label: "RS", align: "right", render: (r) => fmtInt(r.rs_rank) },
  { key: "close", w: 100, label: "Close", align: "right", render: (r) => fmtPrice(r.close) },
  { key: "live", w: 160, label: "Live", align: "right", render: (_r, live) => <LiveCell q={live} /> },
  {
    key: "change_pct",
    w: 90,
    label: "Change",
    align: "right", render: (r) => <span className={tone(r.change_pct)}>{fmtPct(r.change_pct, 2, true)}</span>,
  },
  {
    key: "grade",
    w: 110,
    label: "Setup",
    render: (r) =>
      r.classification && r.grade !== null ? (
        <GradeBadge classification={r.classification} grade={r.grade} />
      ) : (
        <span className="text-mute">{DASH}</span>
      ),
  },
  { key: "score", w: 80, label: "Score", align: "right", render: (r) => fmtNum(r.score, 0) },
  { key: "pivot", w: 100, label: "Pivot", align: "right", render: (r) => fmtPrice(r.pivot) },
  {
    key: "status",
    w: 120,
    label: "Status",
    render: (r) => (r.status ? <StatusPill status={r.status} /> : <span className="text-mute">{DASH}</span>),
  },
  {
    key: "pivot_distance_pct",
    w: 90,
    label: "To pivot",
    align: "right", render: (r) => fmtPct(r.pivot_distance_pct, 1, true),
  },
];

const SORTABLE = new Set([
  "symbol", "rs_rank", "conditions_passed", "close", "change_pct", "score", "grade",
  "pivot_distance_pct",
]); // fmt: skip

const triState = (v: boolean | undefined): string => (v === undefined ? "" : v ? "yes" : "no");
const fromTri = (v: string): boolean | undefined => (v === "" ? undefined : v === "yes");
const numOrUndef = (v: string): number | undefined => (v === "" ? undefined : Number(v));

function Field({ id, label, children, className = "" }: { id: string; label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <label className="mb-1 block text-[11px] uppercase text-mute" htmlFor={id}>
        {label}
      </label>
      {children}
    </div>
  );
}

/** The filter bar above the results: every filter in one place, nothing preset. All run on the server. */
function Filters({
  q,
  stageCounts,
  strategyIds,
  onChange,
  onReset,
}: {
  strategyIds: string[];
  q: ScreenerQuery;
  stageCounts: Record<string, number>;
  onChange: (next: Partial<ScreenerQuery>) => void;
  onReset: () => void;
}) {
  const changed =
    q.q !== "" ||
    q.stages.length > 0 ||
    q.ttPass !== undefined ||
    q.nearHigh !== undefined ||
    q.minRs !== undefined ||
    q.minConditions !== undefined ||
    q.hasSetup ||
    q.minGrade !== undefined ||
    q.status !== undefined ||
    q.classification !== undefined ||
    q.minStrategies !== undefined ||
    q.strategy !== DEFAULT_QUERY.strategy;
  return (
    <Card title="Filters" label="Filters">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Field id="scr-q" label="Symbol or company" className="col-span-2">
          <input
            id="scr-q"
            type="search"
            value={q.q}
            maxLength={40}
            onChange={(e) => onChange({ q: e.target.value })}
            className={field}
            placeholder="e.g. RELIANCE"
          />
        </Field>
        <Field id="scr-strategy" label="Strategy">
          <select id="scr-strategy" value={q.strategy} onChange={(e) => onChange({ strategy: e.target.value })} className={field}>
            {strategyIds.map((id) => (
              <option key={id} value={id}>
                {strategyLabel(id)}
              </option>
            ))}
          </select>
        </Field>
        <Field id="scr-tt" label="Trend Template">
          <select id="scr-tt" value={triState(q.ttPass)} onChange={(e) => onChange({ ttPass: fromTri(e.target.value) })} className={field}>
            <option value="">Any</option>
            <option value="yes">Passes all 10</option>
            <option value="no">Does not pass</option>
          </select>
        </Field>
        <Field id="scr-high" label="Near 52-week high">
          <select id="scr-high" value={triState(q.nearHigh)} onChange={(e) => onChange({ nearHigh: fromTri(e.target.value) })} className={field}>
            <option value="">Any</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </Field>
        <Field id="scr-rs" label="Min RS rank">
          <input
            id="scr-rs"
            type="number"
            min={0}
            max={99}
            value={q.minRs ?? ""}
            onChange={(e) => onChange({ minRs: numOrUndef(e.target.value) })}
            className={field}
          />
        </Field>
        <Field id="scr-cond" label="Min conditions">
          <input
            id="scr-cond"
            type="number"
            min={0}
            max={10}
            value={q.minConditions ?? ""}
            onChange={(e) => onChange({ minConditions: numOrUndef(e.target.value) })}
            className={field}
          />
        </Field>
        <Field id="scr-setup" label="VCP setup">
          <select
            id="scr-setup"
            value={q.hasSetup ? "ranked" : ""}
            onChange={(e) => onChange({ hasSetup: e.target.value === "ranked" })}
            className={field}
          >
            <option value="">Any</option>
            <option value="ranked">Only ranked setups</option>
          </select>
        </Field>
        <Field id="scr-class" label="Setup class">
          <select
            id="scr-class"
            value={q.classification ?? ""}
            onChange={(e) => onChange({ classification: e.target.value || undefined })}
            className={field}
          >
            <option value="">Any</option>
            <option value="A_PLUS_VCP">A+ VCP</option>
            <option value="VCP">VCP</option>
            <option value="VCP_LIKE">VCP like</option>
          </select>
        </Field>
        <Field id="scr-grade" label="Min setup grade">
          <select id="scr-grade" value={q.minGrade ?? ""} onChange={(e) => onChange({ minGrade: numOrUndef(e.target.value) })} className={field}>
            <option value="">Any</option>
            <option value="1">Grade 1+</option>
            <option value="2">Grade 2+</option>
            <option value="3">Grade 3 (A+)</option>
          </select>
        </Field>
        <Field id="scr-status" label="Setup status">
          <select id="scr-status" value={q.status ?? ""} onChange={(e) => onChange({ status: e.target.value || undefined })} className={field}>
            <option value="">Any</option>
            <option value="FORMING">Forming</option>
            <option value="PIVOT_READY">Pivot ready</option>
            <option value="BREAKOUT">Breakout</option>
          </select>
        </Field>
        <Field id="scr-several" label="On strategies">
          <select
            id="scr-several"
            value={q.minStrategies ?? ""}
            onChange={(e) => onChange({ minStrategies: numOrUndef(e.target.value) })}
            className={field}
          >
            <option value="">Any</option>
            <option value="2">2 or more</option>
            <option value="3">3 or more</option>
          </select>
        </Field>
        <fieldset className="col-span-2 md:col-span-4 xl:col-span-3">
          <legend className="mb-1 block text-[11px] uppercase text-mute">Weekly stage</legend>
          <div className="flex flex-wrap gap-1.5">
            {STAGES.map((s) => {
              const on = q.stages.includes(s);
              return (
                <label
                  key={s}
                  className={`flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
                    on ? "border-accent bg-accent/20 text-ink" : "border-line text-mute hover:text-ink"
                  }`}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={on}
                    onChange={(e) => onChange({ stages: e.target.checked ? [...q.stages, s] : q.stages.filter((x) => x !== s) })}
                  />
                  {stageLabel(s)}
                  <span className="text-mute">{fmtInt(stageCounts[s] ?? 0)}</span>
                </label>
              );
            })}
          </div>
        </fieldset>
        <div className="col-span-2 flex items-end justify-end md:col-span-4 xl:col-span-1">
          <button
            type="button"
            disabled={!changed}
            onClick={onReset}
            className="w-full rounded border border-line px-2 py-1.5 text-xs text-mute enabled:hover:text-ink disabled:opacity-40"
          >
            Reset filters
          </button>
        </div>
      </div>
    </Card>
  );
}

function Pages({ page, total, size, onPage }: { page: number; total: number; size: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(total, page * size);
  const btn = "min-w-7 rounded border px-2 py-1 text-xs";
  return (
    <nav aria-label="Pages" className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-mute">
      <span>
        Showing {from}–{to} of {total}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          className={`${btn} border-line enabled:hover:text-ink disabled:opacity-40`}
        >
          ‹
        </button>
        {pageNumbers(page - 1, pages).map((p, i) =>
          p === "…" ? (
            <span key={`gap${i}`} className="px-1">
              …
            </span>
          ) : (
            <button
              key={p}
              type="button"
              aria-label={`Page ${p + 1}`}
              aria-current={p + 1 === page ? "page" : undefined}
              onClick={() => onPage(p + 1)}
              className={`${btn} ${p + 1 === page ? "border-accent bg-accent text-white" : "border-line enabled:hover:text-ink"}`}
            >
              {p + 1}
            </button>
          ),
        )}
        <button
          type="button"
          aria-label="Next page"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          className={`${btn} border-line enabled:hover:text-ink disabled:opacity-40`}
        >
          ›
        </button>
      </div>
    </nav>
  );
}

export function Screener() {
  const [query, setQuery] = useState<ScreenerQuery>(DEFAULT_QUERY);
  const strategies = useStrategies();
  const strategyIds = strategies.data?.strategies.map((x) => x.strategy_id) ?? ["vcp"];
  const result = useScreener(query);
  const data = result.data;
  const live = useLiveQuotes(data?.rows.map((r) => r.symbol) ?? [], !!data);
  const change = (next: Partial<ScreenerQuery>) => setQuery((q) => ({ ...q, ...next, page: 1 }));

  return (
    <div className="flex min-h-screen flex-col">
      <Nav active="Screener" />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Screener</h1>
        <Filters q={query} stageCounts={data?.stage_counts ?? {}} strategyIds={strategyIds} onChange={change} onReset={() => setQuery(DEFAULT_QUERY)} />
        <div className="min-w-0 space-y-3">
          <LiveNotice />
            <Card
              title="Results"
              subtitle={data ? `${fmtInt(data.total)} of ${fmtInt(data.scanned)} stocks match` : undefined}
            >
              {result.isError ? (
                <ErrorBox error={result.error} />
              ) : !data ? (
                <Loading what="screener" />
              ) : data.rows.length === 0 ? (
                <Empty>No stock matches these filters.</Empty>
              ) : (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[1100px] table-fixed text-left text-xs">
                      <colgroup>
                        {COLS.map((c) => (
                          <col key={c.key} style={{ width: c.w }} />
                        ))}
                      </colgroup>
                      <thead className="text-[11px] uppercase text-mute">
                        <tr>
                          {COLS.map((c) => {
                            const key = c.key;
                            const sortable = SORTABLE.has(key);
                            const on = query.sort === key;
                            return (
                              <th
                                key={c.key}
                                scope="col"
                                className={`px-3 py-1.5 font-medium ${alignClass(c)}`}
                                aria-sort={on ? (query.direction === "asc" ? "ascending" : "descending") : "none"}
                              >
                                {sortable ? (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setQuery((q) => ({
                                        ...q,
                                        sort: key,
                                        direction: q.sort === key && q.direction === "desc" ? "asc" : "desc",
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
                        {data.rows.map((r) => (
                          <tr key={r.instrument_id} className="border-t border-line">
                            {COLS.map((c) => (
                              <td key={c.key} className={`px-3 py-1.5 ${alignClass(c)}`}>
                                {c.render(r, live.bySymbol.get(r.symbol))}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Pages
                    page={data.page}
                    total={data.total}
                    size={data.page_size}
                    onPage={(p) => setQuery((q) => ({ ...q, page: p }))}
                  />
                </>
              )}
            </Card>
        </div>
        <p className="text-[11px] text-mute">
          A filter on the daily scan for research, not buy signals. Stages and conditions come from
          the Trend Template; setups from the VCP scan. Nothing here changes a scan or a score.
        </p>
      </main>
      <StatusBar />
    </div>
  );
}
