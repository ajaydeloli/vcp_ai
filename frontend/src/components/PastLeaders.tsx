"use client";

// Past Leaders (owner, 2026-10-23): every 50 %+ run of the stored superperformance study, with its
// launch features, the run and the give-back after the top. Read only; research and display.
import { useEffect, useMemo, useState } from "react";
import { useLeaderRun, useLeaders } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, fmtStamp } from "@/lib/fmt";
import { BAND_LABEL, filterLeaders, leadersTable, NO_FILTER, stageLabel, vcpLabel, type Band, type LeaderFilter } from "@/lib/leaders";
import type { LeaderRow } from "@/lib/schemas";
import { download, toCsv, toXlsx } from "@/lib/tableFiles";
import { LeaderChart } from "./LeaderChart";
import { Page } from "./Page";
import { Field, FIELD, Pages } from "./Screener";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const PAGE_SIZE = 25;
const btn = "rounded border border-line px-2 py-0.5 text-xs text-mute hover:border-accent hover:text-ink disabled:opacity-50";

type Col = {
  key: string;
  label: string;
  title?: string;
  w?: number;
  align?: "left";
  sort?: (r: LeaderRow) => number | string | null;
  render: (r: LeaderRow) => React.ReactNode;
};

const gainTone = (b: LeaderRow["gain_band"]): string => (b === "tripled" ? "text-up font-semibold" : b === "doubled" ? "text-up" : "text-ink");

const COLS: Col[] = [
  {
    key: "symbol",
    label: "Symbol",
    w: 120,
    align: "left",
    sort: (r) => r.symbol,
    render: (r) => (
      <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium text-accent hover:underline">
        {r.symbol}
      </a>
    ),
  },
  { key: "sector", label: "Sector", w: 150, align: "left", sort: (r) => r.sector ?? "", render: (r) => <span className="text-mute">{r.sector ?? "Unclassified"}</span> },
  {
    key: "launch",
    label: "Launch",
    title: "The low the run started from (adjusted close)",
    sort: (r) => r.launch_date,
    render: (r) => (
      <span>
        {fmtDay(r.launch_date)}
        <span className="block text-mute">{fmtPrice(r.launch_close)}</span>
      </span>
    ),
  },
  {
    key: "peak",
    label: "Peak",
    title: "Highest close within 252 sessions of the launch",
    sort: (r) => r.peak_date,
    render: (r) => (
      <span>
        {fmtDay(r.peak_date)}
        <span className="block text-mute">{fmtPrice(r.peak_close)}</span>
      </span>
    ),
  },
  {
    key: "gain",
    label: "Gain",
    sort: (r) => r.gain_pct,
    render: (r) => (
      <span className={gainTone(r.gain_band)} title={r.complete ? undefined : "The 252 sessions after the launch are not all known yet: the peak may still move"}>
        {fmtPct(r.gain_pct, 0, true)}
        {r.complete ? "" : " *"}
      </span>
    ),
  },
  { key: "days", label: "Days to peak", title: "Sessions from the launch to the peak", sort: (r) => r.sessions_to_peak, render: (r) => fmtInt(r.sessions_to_peak) },
  { key: "pullback", label: "Max pullback", title: "Deepest close below a high inside the run", sort: (r) => r.max_pullback_pct, render: (r) => fmtPct(r.max_pullback_pct, 0) },
  { key: "stage", label: "Stage", title: "Weekly stage on the launch day", sort: (r) => r.launch_stage, render: (r) => stageLabel(r.launch_stage) ?? DASH },
  { key: "rs", label: "RS", title: "RS rank on the launch day", sort: (r) => r.launch_rs, render: (r) => fmtInt(r.launch_rs) },
  {
    key: "tt",
    label: "TT",
    title: "Trend Template on the launch day",
    sort: (r) => (r.launch_tt === null ? null : r.launch_tt ? 1 : 0),
    render: (r) => (r.launch_tt === null ? DASH : r.launch_tt ? <span className="text-up">Pass</span> : <span className="text-mute">Fail</span>),
  },
  {
    key: "base",
    label: "Base",
    title: "The base before the first breakout after the launch: sessions and depth",
    sort: (r) => r.base_depth_pct,
    render: (r) => (r.base_length === null ? DASH : `${fmtInt(r.base_length)} d, ${fmtPct(r.base_depth_pct, 0)}`),
  },
  {
    key: "vcp",
    label: "VCP",
    title: "Our detector's weekly scan before the first breakout (No scan: the stock was not in that week's scan)",
    sort: (r) => vcpLabel(r.vcp_before_breakout),
    render: (r) => <span className={r.vcp_before_breakout === "VCP" ? "text-accent" : "text-mute"}>{vcpLabel(r.vcp_before_breakout)}</span>,
  },
  {
    key: "giveback",
    label: "Give-back",
    title: "Deepest close below the peak in the 252 sessions after it, and the share of the gain that was",
    sort: (r) => r.giveback_pct,
    render: (r) =>
      r.giveback_pct === null ? (
        DASH
      ) : (
        <span title={r.giveback_final ? undefined : "The year after the peak is not over yet"}>
          {fmtPct(-r.giveback_pct, 0, true)}
          <span className="block text-mute">{`${fmtNum(r.giveback_of_gain_pct, 0)} % of gain${r.giveback_final ? "" : " so far"}`}</span>
        </span>
      ),
  },
];

const align = (c: Pick<Col, "align">): string => (c.align === "left" ? "text-left" : "text-center");

function Export({ rows }: { rows: LeaderRow[] }) {
  const [busy, setBusy] = useState<"csv" | "xlsx" | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run(kind: "csv" | "xlsx") {
    setBusy(kind);
    setError(null);
    try {
      const { header, body } = leadersTable(rows);
      if (kind === "csv") download(new Blob([toCsv(header, body)], { type: "text/csv;charset=utf-8" }), "past_leaders.csv");
      else download(await toXlsx(header, body, "Past leaders"), "past_leaders.xlsx");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(null);
    }
  }
  return (
    <span className="inline-flex items-center gap-1.5" role="group" aria-label="Export runs">
      <span className="text-xs text-mute">Export</span>
      <button type="button" className={btn} disabled={busy !== null || rows.length === 0} onClick={() => run("csv")} title="All matching runs, every column, as CSV">
        {busy === "csv" ? "…" : "CSV"}
      </button>
      <button type="button" className={btn} disabled={busy !== null || rows.length === 0} onClick={() => run("xlsx")} title="All matching runs, every column, as Excel (.xlsx)">
        {busy === "xlsx" ? "…" : "Excel"}
      </button>
      {error ? (
        <span role="alert" className="text-xs text-down">
          {error}
        </span>
      ) : null}
    </span>
  );
}

function RunChart({ runId, onClose }: { runId: number; onClose: () => void }) {
  const run = useLeaderRun(runId);
  const r = run.data?.run;
  useEffect(() => {
    document.getElementById("leader-chart")?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [runId]);
  return (
    <div id="leader-chart" className="scroll-mt-4">
      <Card
        title={r ? `${r.symbol}: ${fmtPct(r.gain_pct, 0, true)} from ${fmtDay(r.launch_date)} to ${fmtDay(r.peak_date)}` : "Run chart"}
        subtitle="Launch (▲), the bases on the way up (orange line at each base high, B1, B2 … at the breakout) and the top (▼). Daily candles, 50-day average in blue."
        right={
          <button type="button" className={btn} onClick={onClose}>
            Close chart
          </button>
        }
      >
        {run.isError ? (
          <ErrorBox error={run.error} />
        ) : run.isPending ? (
          <Loading what="the run's chart" />
        ) : run.data.bars.length === 0 ? (
          <Empty>No prices stored for this stock.</Empty>
        ) : (
          <>
            <LeaderChart data={run.data} />
            <ul aria-label="Bases" className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-mute">
              {run.data.bases.length === 0 ? (
                <li>No base of 10 sessions or more between the launch and the top.</li>
              ) : (
                run.data.bases.map((b) => (
                  <li key={b.seq}>{`B${b.seq}: ${fmtDay(b.breakout_date)} above ${fmtPrice(b.base_high)} (${fmtInt(b.length)} sessions, ${fmtPct(b.depth_pct, 0)} deep)`}</li>
                ))
              )}
            </ul>
          </>
        )}
      </Card>
    </div>
  );
}

/** Every 50 %+ run since 2022, filterable, sortable and exportable; a run's chart on request. */
export function PastLeaders() {
  const leaders = useLeaders();
  const [f, setF] = useState<LeaderFilter>(NO_FILTER);
  const [sort, setSort] = useState("launch");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [chart, setChart] = useState<number | null>(null);

  const all = useMemo(() => leaders.data?.runs ?? [], [leaders.data]);
  const rows = useMemo(() => {
    const by = COLS.find((c) => c.key === sort)?.sort ?? ((r: LeaderRow) => r.launch_date);
    const sign = dir === "asc" ? 1 : -1;
    return filterLeaders(all, f).sort((a, b) => {
      const x = by(a);
      const y = by(b);
      if (x === null) return y === null ? 0 : 1; // a missing value goes last either way
      if (y === null) return -1;
      return x < y ? -sign : x > y ? sign : 0;
    });
  }, [all, f, sort, dir]);
  const set = (patch: Partial<LeaderFilter>) => {
    setF((old) => ({ ...old, ...patch }));
    setPage(1);
  };
  const at = Math.min(page, Math.max(1, Math.ceil(rows.length / PAGE_SIZE)));
  const shown = rows.slice((at - 1) * PAGE_SIZE, at * PAGE_SIZE);
  const study = leaders.data?.study ?? null;
  const changed = JSON.stringify(f) !== JSON.stringify(NO_FILTER);

  return (
    <Page active="Past Leaders" title="Past Leaders">
      <Card title="Past leaders" label="Study">
        {leaders.isError ? (
          <ErrorBox error={leaders.error} />
        ) : leaders.isPending ? (
          <Loading what="the study" />
        ) : study === null ? (
          <Empty>No study stored yet. Run: vcp research superperformance</Empty>
        ) : (
          <p className="text-xs text-mute">
            {`Every run of 50 % or more within 252 sessions, from an eligible low, in the point-in-time universe. ${fmtInt(study.runs_total)} runs, prices to ${fmtDay(study.data_end)}; study of ${fmtStamp(study.created_at)}. `}
            <span title={study.survivorship_detail ?? undefined}>{`Survivorship ${study.survivorship_status.toLowerCase()}. `}</span>
            {"Research and display only: uses every year, including the validation window. * = the 252 sessions after the launch are not all known yet."}
          </p>
        )}
      </Card>

      <Card title="Filters" label="Filters">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,1.3fr)_minmax(0,1.3fr)_auto_auto]">
          <Field id="pl-q" label="Symbol">
            <input id="pl-q" type="search" value={f.q} maxLength={30} onChange={(e) => set({ q: e.target.value })} className={FIELD} placeholder="e.g. BSE" />
          </Field>
          <Field id="pl-year" label="Launch year">
            <select id="pl-year" value={f.year} onChange={(e) => set({ year: e.target.value })} className={FIELD}>
              <option value="">All</option>
              {(leaders.data?.years ?? []).map((y) => (
                <option key={y} value={String(y)}>
                  {y}
                </option>
              ))}
            </select>
          </Field>
          <Field id="pl-sector" label="Sector">
            <select id="pl-sector" value={f.sector} onChange={(e) => set({ sector: e.target.value })} className={FIELD}>
              <option value="">All</option>
              {(leaders.data?.sectors ?? []).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
          <Field id="pl-band" label="Gain">
            <select id="pl-band" value={f.band} onChange={(e) => set({ band: e.target.value as Band })} className={FIELD}>
              <option value="">All (+50 % or more)</option>
              {(Object.keys(BAND_LABEL) as LeaderRow["gain_band"][]).map((b) => (
                <option key={b} value={b}>
                  {BAND_LABEL[b]}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex items-end gap-2 pb-1.5 text-xs text-ink" title="Our VCP detector flagged the stock in the weekly scan before the first breakout after the launch">
            <input type="checkbox" checked={f.vcp} onChange={(e) => set({ vcp: e.target.checked })} />
            VCP at launch
          </label>
          <div className="flex items-end">
            <button
              type="button"
              disabled={!changed}
              onClick={() => set(NO_FILTER)}
              className="w-full whitespace-nowrap rounded border border-line px-2 py-1.5 text-xs text-mute enabled:hover:text-ink disabled:opacity-40"
            >
              Reset filters
            </button>
          </div>
        </div>
      </Card>

      {chart !== null ? <RunChart runId={chart} onClose={() => setChart(null)} /> : null}

      <Card
        title="Runs"
        subtitle="Click a row's Chart button for its chart. Symbol opens Stock Analysis."
        right={
          leaders.data?.study ? (
            <span className="flex flex-wrap items-center justify-end gap-3">
              <span className="text-xs text-mute">{`${fmtInt(rows.length)} of ${fmtInt(all.length)} runs`}</span>
              <Export rows={rows} />
            </span>
          ) : undefined
        }
      >
        {leaders.isError ? (
          <ErrorBox error={leaders.error} />
        ) : leaders.isPending ? (
          <Loading what="runs" />
        ) : rows.length === 0 ? (
          <Empty>{all.length === 0 ? "No runs stored." : "No run matches these filters."}</Empty>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1250px] table-fixed text-left text-xs">
                <colgroup>
                  {COLS.map((c) => (
                    <col key={c.key} style={c.w ? { width: c.w } : undefined} />
                  ))}
                  <col style={{ width: 70 }} />
                </colgroup>
                <thead className="text-[11px] uppercase text-mute">
                  <tr>
                    {COLS.map((c) => {
                      const on = sort === c.key;
                      return (
                        <th
                          key={c.key}
                          scope="col"
                          title={c.title}
                          className={`px-2 py-1.5 font-medium ${align(c)}`}
                          aria-sort={on ? (dir === "asc" ? "ascending" : "descending") : "none"}
                        >
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
                        </th>
                      );
                    })}
                    <th scope="col" className="px-2 py-1.5 font-medium">
                      <span className="sr-only">Chart</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={r.run_id} className={`border-t border-line ${chart === r.run_id ? "bg-accent/10" : ""}`}>
                      {COLS.map((c) => (
                        <td key={c.key} className={`px-2 py-1.5 align-top ${align(c)}`}>
                          {c.render(r)}
                        </td>
                      ))}
                      <td className="px-2 py-1.5 text-center align-top">
                        <button
                          type="button"
                          className={btn}
                          aria-pressed={chart === r.run_id}
                          aria-label={`Chart of ${r.symbol}'s run from ${r.launch_date}`}
                          onClick={() => setChart(chart === r.run_id ? null : r.run_id)}
                        >
                          Chart
                        </button>
                      </td>
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
