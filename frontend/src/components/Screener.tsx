"use client";

import { useState } from "react";
import { useScreener, useStrategies } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, strategyLabel, tone } from "@/lib/fmt";
import { pageNumbers } from "@/lib/lists";
import { DEFAULT_QUERY, PRESETS, presetQuery, STAGES, type ScreenerQuery } from "@/lib/screener";
import type { ScreenerRow } from "@/lib/schemas";
import { Nav } from "./Nav";
import { WatchStar } from "./WatchStar";
import { StatusBar } from "./StatusBar";
import { TopBar } from "./TopBar";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";

const field = "w-full rounded border border-line bg-panel2 px-2 py-1.5 text-xs text-ink";
const stageLabel = (s: string): string => s.replace("STAGE_", "Stage ").replace("TRANSITION", "Transition");

export type Col = {
  key: string;
  label: string;
  align?: "right";
  render: (r: ScreenerRow) => React.ReactNode;
};

const yesNo = (v: boolean | null) =>
  v === null ? <span className="text-mute">{DASH}</span> : v ? <span className="text-up">Yes</span> : <span className="text-mute">No</span>;

export const COLS: Col[] = [
  {
    key: "watch",
    label: "",
    render: (r) => <WatchStar symbol={r.symbol} />,
  },
  {
    key: "symbol",
    label: "Symbol",
    render: (r) => (
      <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium text-accent hover:underline">
        {r.symbol}
      </a>
    ),
  },
  {
    key: "company",
    label: "Company",
    render: (r) => <span className="block max-w-44 truncate text-ink">{r.company ?? DASH}</span>,
  },
  { key: "stage", label: "Stage", render: (r) => (r.stage ? stageLabel(r.stage) : DASH) },
  { key: "trend_score", label: "Trend", align: "right", render: (r) => fmtNum(r.trend_score, 0) },
  {
    key: "conditions_passed",
    label: "Conditions",
    align: "right",
    render: (r) =>
      r.conditions_passed === null ? DASH : `${r.conditions_passed}/${r.conditions_total ?? DASH}`,
  },
  { key: "near_high", label: "Near high", render: (r) => yesNo(r.near_52w_high) },
  { key: "rs_rank", label: "RS", align: "right", render: (r) => fmtInt(r.rs_rank) },
  { key: "close", label: "Price", align: "right", render: (r) => fmtPrice(r.close) },
  {
    key: "change_pct",
    label: "Change",
    align: "right",
    render: (r) => <span className={tone(r.change_pct)}>{fmtPct(r.change_pct, 2, true)}</span>,
  },
  {
    key: "grade",
    label: "Setup",
    render: (r) =>
      r.classification && r.grade !== null ? (
        <GradeBadge classification={r.classification} grade={r.grade} />
      ) : (
        <span className="text-mute">{DASH}</span>
      ),
  },
  { key: "score", label: "Score", align: "right", render: (r) => fmtNum(r.score, 0) },
  { key: "pivot", label: "Pivot", align: "right", render: (r) => fmtPrice(r.pivot) },
  {
    key: "status",
    label: "Status",
    render: (r) => (r.status ? <StatusPill status={r.status} /> : <span className="text-mute">{DASH}</span>),
  },
  {
    key: "pivot_distance_pct",
    label: "To pivot",
    align: "right",
    render: (r) => fmtPct(r.pivot_distance_pct, 1, true),
  },
];

const SORTABLE = new Set([
  "symbol", "rs_rank", "trend_score", "conditions_passed", "close", "change_pct", "score", "grade",
  "pivot_distance_pct",
]); // fmt: skip

const triState = (v: boolean | undefined): string => (v === undefined ? "" : v ? "yes" : "no");
const fromTri = (v: string): boolean | undefined => (v === "" ? undefined : v === "yes");
const numOrUndef = (v: string): number | undefined => (v === "" ? undefined : Number(v));

function Filters({
  q,
  stageCounts,
  strategyIds,
  onChange,
}: {
  strategyIds: string[];
  q: ScreenerQuery;
  stageCounts: Record<string, number>;
  onChange: (next: Partial<ScreenerQuery>) => void;
}) {
  const label = "mb-1 block text-[11px] uppercase text-mute";
  return (
    <Card title="Filters" label="Filters">
      <div className="space-y-3">
        <div>
          <label className={label} htmlFor="scr-q">
            Symbol or company
          </label>
          <input
            id="scr-q"
            type="search"
            value={q.q}
            maxLength={40}
            onChange={(e) => onChange({ q: e.target.value })}
            className={field}
            placeholder="e.g. RELIANCE"
          />
        </div>
        <fieldset>
          <legend className={label}>Weekly stage</legend>
          <div className="space-y-1">
            {STAGES.map((s) => (
              <label key={s} className="flex items-center justify-between gap-2 text-xs text-ink">
                <span className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={q.stages.includes(s)}
                    onChange={(e) =>
                      onChange({ stages: e.target.checked ? [...q.stages, s] : q.stages.filter((x) => x !== s) })
                    }
                  />
                  {stageLabel(s)}
                </span>
                <span className="text-mute">{fmtInt(stageCounts[s] ?? 0)}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <div>
          <label className={label} htmlFor="scr-tt">
            Trend Template
          </label>
          <select
            id="scr-tt"
            value={triState(q.ttPass)}
            onChange={(e) => onChange({ ttPass: fromTri(e.target.value) })}
            className={field}
          >
            <option value="">Any</option>
            <option value="yes">Passes all 10</option>
            <option value="no">Does not pass</option>
          </select>
        </div>
        <div>
          <label className={label} htmlFor="scr-high">
            Near 52-week high
          </label>
          <select
            id="scr-high"
            value={triState(q.nearHigh)}
            onChange={(e) => onChange({ nearHigh: fromTri(e.target.value) })}
            className={field}
          >
            <option value="">Any</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className={label} htmlFor="scr-rs">
              Min RS rank
            </label>
            <input
              id="scr-rs"
              type="number"
              min={0}
              max={99}
              value={q.minRs ?? ""}
              onChange={(e) => onChange({ minRs: numOrUndef(e.target.value) })}
              className={field}
            />
          </div>
          <div>
            <label className={label} htmlFor="scr-cond">
              Min conditions
            </label>
            <input
              id="scr-cond"
              type="number"
              min={0}
              max={10}
              value={q.minConditions ?? ""}
              onChange={(e) => onChange({ minConditions: numOrUndef(e.target.value) })}
              className={field}
            />
          </div>
        </div>
        <div>
          <label className={label} htmlFor="scr-strategy">
            Strategy
          </label>
          <select
            id="scr-strategy"
            value={q.strategy}
            onChange={(e) => onChange({ strategy: e.target.value })}
            className={field}
          >
            {strategyIds.map((id) => (
              <option key={id} value={id}>
                {strategyLabel(id)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={label} htmlFor="scr-class">
            Setup class
          </label>
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
        </div>
        <div>
          <label className={label} htmlFor="scr-setup">
            VCP setup
          </label>
          <select
            id="scr-setup"
            value={q.hasSetup ? "ranked" : ""}
            onChange={(e) => onChange({ hasSetup: e.target.value === "ranked" })}
            className={field}
          >
            <option value="">Any</option>
            <option value="ranked">Only ranked setups</option>
          </select>
        </div>
        <div>
          <label className={label} htmlFor="scr-grade">
            Min setup grade
          </label>
          <select
            id="scr-grade"
            value={q.minGrade ?? ""}
            onChange={(e) => onChange({ minGrade: numOrUndef(e.target.value) })}
            className={field}
          >
            <option value="">Any</option>
            <option value="1">Grade 1+</option>
            <option value="2">Grade 2+</option>
            <option value="3">Grade 3 (A+)</option>
          </select>
        </div>
        <div>
          <label className={label} htmlFor="scr-status">
            Setup status
          </label>
          <select
            id="scr-status"
            value={q.status ?? ""}
            onChange={(e) => onChange({ status: e.target.value || undefined })}
            className={field}
          >
            <option value="">Any</option>
            <option value="FORMING">Forming</option>
            <option value="PIVOT_READY">Pivot ready</option>
            <option value="BREAKOUT">Breakout</option>
          </select>
        </div>
        <button
          type="button"
          onClick={() => onChange({ ...DEFAULT_QUERY })}
          className="w-full rounded border border-line px-2 py-1.5 text-xs text-mute hover:text-ink"
        >
          Reset filters
        </button>
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
  const change = (next: Partial<ScreenerQuery>) => setQuery((q) => ({ ...q, ...next, page: 1 }));
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  const active = PRESETS.find((p) => same({ ...presetQuery(p, query.q, query.strategy), page: 1 }, { ...query, page: 1 }));

  return (
    <div className="flex min-h-screen">
      <Nav active="Screener" />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Screener</h1>
        <TopBar onPick={(symbol) => window.location.assign(`/stocks/${encodeURIComponent(symbol)}`)} />
        <Card
          title="Screener"
          subtitle={
            data
              ? `${fmtInt(data.scanned)} stocks scanned on ${fmtDay(data.as_of)} · setups of the ${strategyLabel(data.strategy_id)} strategy`
              : "Every stock of the daily scan"
          }
        >
          <div className="flex flex-wrap gap-2" role="group" aria-label="Presets">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                title={p.hint}
                aria-pressed={active?.id === p.id}
                onClick={() => setQuery(presetQuery(p, query.q, query.strategy))}
                className={`rounded-full border px-3 py-1 text-xs ${
                  active?.id === p.id ? "border-accent bg-accent text-white" : "border-line text-mute hover:text-ink"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </Card>
        <div className="grid gap-4 xl:grid-cols-12">
          <div className="min-w-0 xl:col-span-3">
            <Filters q={query} stageCounts={data?.stage_counts ?? {}} strategyIds={strategyIds} onChange={change} />
          </div>
          <div className="min-w-0 xl:col-span-9">
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
                    <table className="w-full text-left text-xs">
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
                                className={`px-2 py-1.5 font-medium ${c.align === "right" ? "text-right" : ""}`}
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
                              <td key={c.key} className={`px-2 py-1.5 ${c.align === "right" ? "text-right" : ""}`}>
                                {c.render(r)}
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
