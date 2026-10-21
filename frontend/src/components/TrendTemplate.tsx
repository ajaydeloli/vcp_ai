"use client";

// The Trend Template page (owner, 2026-10-21): the whole scan through the ten conditions. Which
// condition keeps most stocks out, who started or stopped passing since the previous scan, who
// fails exactly one condition, who moved into weekly Stage 2 this week, and the stage by sector.
// The daily pass share over time is on the Dashboard (Leadership card), not repeated here.
import { useMemo, useState } from "react";
import { useTrendTemplate } from "@/lib/api";
import { fmtDay, fmtInt, fmtPrice } from "@/lib/fmt";
import type { TrendTemplateData, TTStock } from "@/lib/schemas";
import { Page } from "./Page";
import { conditionLabel, Funnel, gap, StageBars, stageName } from "./TrendTemplateParts";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const link = (s: string) => `/stocks/${encodeURIComponent(s)}`;

function Headline({ d }: { d: TrendTemplateData }) {
  const delta = d.passing_previous === null ? null : d.passing - d.passing_previous;
  const tiles: [string, string, string | null][] = [
    ["Passing all 10", fmtInt(d.passing), delta === null ? null : `${delta >= 0 ? "▲" : "▼"} ${Math.abs(delta)} since ${fmtDay(d.previous)}`],
    ["Pass rate", d.population ? `${((100 * d.passing) / d.population).toFixed(1)}%` : "–", `of ${fmtInt(d.population)} stocks with enough data`],
    ["New today", fmtInt(d.new_passers.length), `${fmtInt(d.dropped.length)} dropped`],
    ["One condition short", fmtInt(d.nearly_total), "fail exactly one condition"],
  ];
  return (
    <section aria-label="Trend Template today" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map(([label, value, sub]) => (
        <div key={label} className="rounded-lg border border-line bg-panel p-3">
          <p className="text-xs text-mute">{label}</p>
          <p className="text-2xl font-semibold tabular-nums text-ink">{value}</p>
          {sub ? <p className={`text-xs ${label === "Passing all 10" && delta !== null ? (delta >= 0 ? "text-up" : "text-down") : "text-mute"}`}>{sub}</p> : null}
        </div>
      ))}
    </section>
  );
}

function StockList({ rows, empty, extra }: { rows: TTStock[]; empty: string; extra?: (s: TTStock) => React.ReactNode }) {
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <ul className="divide-y divide-line text-xs">
      {rows.map((s) => (
        <li key={s.symbol} className="flex items-baseline gap-2 py-1.5">
          <a href={link(s.symbol)} className="w-28 shrink-0 truncate font-semibold text-accent hover:underline">
            {s.symbol}
          </a>
          <span className="w-12 shrink-0 tabular-nums text-ink" title="RS rank">
            RS {s.rs_rank ?? "–"}
          </span>
          <span className="min-w-0 flex-1 truncate text-mute">{extra ? extra(s) : (s.sector ?? "")}</span>
          <span className="shrink-0 tabular-nums text-mute">{fmtPrice(s.close)}</span>
        </li>
      ))}
    </ul>
  );
}

function Nearly({ d }: { d: TrendTemplateData }) {
  const [only, setOnly] = useState<string | null>(null);
  const counts = useMemo(() => d.conditions.filter((c) => c.only_fail > 0).sort((a, b) => b.only_fail - a.only_fail), [d]);
  const rows = only ? d.nearly.filter((s) => s.failed[0]?.name === only) : d.nearly;
  return (
    <Card
      title="One condition short"
      subtitle={`Pass 9 of 10 conditions, strongest RS first${d.nearly_total > d.nearly.length ? ` (top ${d.nearly.length} of ${d.nearly_total})` : ""}. Tomorrow's candidates.`}
    >
      {counts.length > 1 ? (
        <div className="mb-2 flex flex-wrap gap-1.5 text-xs" role="group" aria-label="Failing condition">
          <button
            type="button"
            aria-pressed={only === null}
            onClick={() => setOnly(null)}
            className={`rounded border px-2 py-0.5 ${only === null ? "border-accent text-ink" : "border-line text-mute"}`}
          >
            All
          </button>
          {counts.map((c) => (
            <button
              key={c.name}
              type="button"
              aria-pressed={only === c.name}
              onClick={() => setOnly(c.name)}
              className={`rounded border px-2 py-0.5 ${only === c.name ? "border-accent text-ink" : "border-line text-mute"}`}
            >
              {conditionLabel(c.name)} ({c.only_fail})
            </button>
          ))}
        </div>
      ) : null}
      {rows.length === 0 ? (
        <Empty>No stock fails just one condition.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="text-mute">
              <tr>
                <th className="py-1 font-normal">Symbol</th>
                <th className="py-1 font-normal">Sector</th>
                <th className="py-1 text-right font-normal">RS</th>
                <th className="py-1 text-right font-normal">Close</th>
                <th className="py-1 pl-4 font-normal">Fails</th>
                <th className="py-1 font-normal">Value</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.symbol} className="border-t border-line">
                  <td className="py-1">
                    <a href={link(s.symbol)} className="font-semibold text-accent hover:underline">
                      {s.symbol}
                    </a>
                  </td>
                  <td className="max-w-[12rem] truncate py-1 text-mute">{s.sector ?? "–"}</td>
                  <td className="py-1 text-right tabular-nums text-ink">{s.rs_rank ?? "–"}</td>
                  <td className="py-1 text-right tabular-nums text-ink">{fmtPrice(s.close)}</td>
                  <td className="py-1 pl-4 text-warn">{s.failed[0] ? conditionLabel(s.failed[0].name) : "–"}</td>
                  <td className="py-1 tabular-nums text-mute">{s.failed[0] ? gap(s.failed[0]) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function TrendTemplatePage() {
  const q = useTrendTemplate();
  const d = q.data;
  return (
    <Page active="Trend Template" title="Trend Template">
      {q.isLoading ? <Loading what="Trend Template" /> : null}
      {q.error ? <ErrorBox error={q.error} /> : null}
      {d && d.as_of === null ? <Empty>No scan yet.</Empty> : null}
      {d && d.as_of !== null ? (
        <>
          <Headline d={d} />
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <Card
              title="Where stocks drop out"
              subtitle={
                <>
                  Stocks passing each condition on {fmtDay(d.as_of)}. &quot;Only&quot;: stocks failing that condition and no other. The pass share over time is on the{" "}
                  <a href="/dashboard" className="text-accent hover:underline">
                    Dashboard
                  </a>{" "}
                  (Leadership).
                </>
              }
            >
              <Funnel d={d} />
            </Card>
            <div className="grid gap-4 sm:grid-cols-2">
              <Card title="New today" subtitle={d.previous ? `Pass all 10 now, did not on ${fmtDay(d.previous)}` : "No earlier scan to compare"}>
                <StockList rows={d.new_passers} empty="No new stock passes today." />
              </Card>
              <Card title="Dropped today" subtitle={d.previous ? `Passed on ${fmtDay(d.previous)}, not now` : "No earlier scan to compare"}>
                <StockList
                  rows={d.dropped}
                  empty="No stock dropped out today."
                  extra={(s) => (s.failed.length ? `fails: ${s.failed.map((f) => conditionLabel(f.name)).join(", ")}` : "no longer scanned")}
                />
              </Card>
            </div>
          </div>
          <Nearly d={d} />
          <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <Card title="Into Stage 2 this week" subtitle={d.week_ago ? `Weekly Stage 2 now, not on ${fmtDay(d.week_ago)}` : "No earlier scan to compare"}>
              <StockList
                rows={d.into_stage2}
                empty="No stock moved into Stage 2 this week."
                extra={(s) => `from ${stageName(s.previous_stage)}${s.sector ? ` · ${s.sector}` : ""}`}
              />
            </Card>
            <Card title="Weekly stage by sector" subtitle="Sectors with the largest Stage 2 share first">
              <StageBars d={d} />
            </Card>
          </div>
        </>
      ) : null}
    </Page>
  );
}
