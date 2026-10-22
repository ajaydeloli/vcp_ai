"use client";

// The research additions to the AI Agent page (owner, 2026-10-22): where each hypothesis stands
// on the road to promotion, the figures behind its verdict as charts, and the review cycle.
// Read only, from the lab's stored runs (data/agent/agent.duckdb).
import { fmtDay } from "@/lib/fmt";
import type { AgentData, AgentHypothesis, AgentRun } from "@/lib/schemas";

const pct = (x: number | null | undefined, d = 1) => (x === null || x === undefined ? "–" : `${x.toFixed(d)}%`);
const ic = (x: number | null | undefined) => (x === null || x === undefined ? "–" : `${x >= 0 ? "+" : ""}${x.toFixed(3)}`);

/** The review cycle: countdown, hypotheses left per strategy, the promotion bar. */
export function ReviewCycle({ d }: { d: AgentData }) {
  const r = d.review;
  if (!r) return null;
  const supported = d.hypotheses.filter((h) => h.outcome === "supported").length;
  return (
    <div className="mb-3 grid gap-3 rounded-lg border border-line bg-panel2/40 p-3 text-xs sm:grid-cols-3" aria-label="Review cycle">
      <div>
        <p className="text-mute">Next strategy review</p>
        <p className="text-lg font-semibold tabular-nums text-ink">{r.days_to_review} days</p>
        <p className="text-mute">
          from {fmtDay(r.review_from)} (cycle {r.cycle})
        </p>
      </div>
      <div>
        <p className="text-mute">Hypotheses left this cycle</p>
        <ul className="mt-1 space-y-0.5">
          {Object.entries(d.budget_left).map(([s, left]) => (
            <li key={s} className="flex items-center gap-2" aria-label={`${s.replace(/_/g, " ")}: ${left} of ${r.max_per_strategy} left`}>
              <span className="w-32 truncate text-ink">{s.replace(/_/g, " ")}</span>
              <span className="flex gap-0.5" aria-hidden="true">
                {Array.from({ length: r.max_per_strategy }, (_, i) => (
                  <span key={i} className={`inline-block h-2 w-3 rounded-sm ${i < r.max_per_strategy - left ? "bg-accent" : "bg-line"}`} />
                ))}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="text-mute">Promotion bar</p>
        <p className="text-ink">
          A supported hypothesis runs in the shadow ledger: at least {r.promotion_min_trades} trades and {r.promotion_min_months} months, then the owner decides at the review.
        </p>
        <p className="mt-1 text-mute">{supported ? `${supported} supported, in shadow trading.` : "No hypothesis supported yet, so nothing is in shadow trading."}</p>
      </div>
    </div>
  );
}

type Step = { label: string; state: "done" | "now" | "todo" | "skip" };

/** Registered → development → validation → verdict → shadow → review, for one hypothesis. */
export function steps(h: AgentHypothesis): Step[] {
  const ok = (w: string) => h.runs.some((r) => r.window === w && r.ok);
  const dev = ok("development");
  const val = ok("validation");
  const done = h.status === "CONCLUDED";
  const supported = h.outcome === "supported";
  const verdict = done ? (h.outcome ? `Verdict: ${h.outcome}` : "Verdict") : "Verdict";
  return [
    { label: "Registered", state: "done" },
    { label: "Development test", state: dev ? "done" : done ? "skip" : "now" },
    { label: "Validation (once)", state: val ? "done" : done ? "skip" : dev ? "now" : "todo" },
    { label: verdict, state: done ? "done" : "todo" },
    { label: "Shadow trading", state: supported ? "now" : done ? "skip" : "todo" },
    { label: "Review", state: supported ? "todo" : done ? "skip" : "todo" },
  ];
}

const STEP_CLASS: Record<Step["state"], string> = {
  done: "border-up/50 bg-up/10 text-up",
  now: "border-accent bg-accent/10 text-ink",
  todo: "border-line text-mute",
  skip: "border-line text-mute line-through opacity-60",
};

export function Pipeline({ h }: { h: AgentHypothesis }) {
  return (
    <ol className="mt-2 flex flex-wrap items-center gap-1 text-[11px]" aria-label={`Progress of H${h.hypothesis_id}`}>
      {steps(h).map((s, i) => (
        <li key={s.label} className="flex items-center gap-1">
          {i > 0 ? (
            <span aria-hidden="true" className="text-mute">
              →
            </span>
          ) : null}
          <span className={`rounded border px-1.5 py-0.5 ${STEP_CLASS[s.state]}`} title={s.state === "skip" ? "Not needed: the hypothesis was concluded" : undefined}>
            {s.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** One horizontal bar per row on a shared scale; ``reference`` draws a dashed reference line. */
function Bars({
  rows,
  reference: refLine,
  fmt,
  label,
}: {
  rows: { name: string; value: number | null; base?: boolean }[];
  reference?: { name: string; value: number };
  fmt: (x: number | null) => string;
  label: string;
}) {
  const vals = rows.map((r) => r.value ?? 0).concat(refLine ? [refLine.value] : []);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const x = (v: number) => ((v - lo) / span) * 100;
  return (
    <figure className="text-[11px]">
      <figcaption className="mb-1 text-mute">{label}</figcaption>
      <ul className="space-y-1">
        {rows.map((r) => {
          const v = r.value ?? 0;
          const left = Math.min(x(0), x(v));
          return (
            <li key={r.name} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_3.5rem] items-center gap-2">
              <span className={`truncate ${r.base ? "text-mute" : "text-ink"}`} title={r.name}>
                {r.name}
              </span>
              <span className="relative h-3 rounded bg-line/40">
                <span
                  className={`absolute top-0 h-3 rounded ${r.base ? "bg-mute/50" : v < 0 ? "bg-down/70" : "bg-accent/70"}`}
                  style={{ left: `${left}%`, width: `${Math.abs(x(v) - x(0))}%` }}
                />
                {refLine ? <span className="absolute -top-0.5 h-4 border-l border-dashed border-warn" style={{ left: `${x(refLine.value)}%` }} /> : null}
              </span>
              <span className="text-right tabular-nums text-ink">{fmt(r.value)}</span>
            </li>
          );
        })}
      </ul>
      {refLine ? (
        <p className="mt-1 text-mute">
          <span className="text-warn">┆</span> {refLine.name}: {fmt(refLine.value)}
        </p>
      ) : null}
    </figure>
  );
}

/** The score variants of a hypothesis against the frozen score, on the same setups. */
export function WhatIfCharts({ runs }: { runs: AgentRun[] }) {
  const wi = runs.filter((r) => r.ok && r.what_if);
  const first = wi[0]?.what_if;
  if (!first) return null;
  const variant = (r: AgentRun) => `V${wi.indexOf(r) + 1}: ${r.change ?? `run ${r.run_id}`}`;
  const n = first.top_n ?? 5;
  const h = first.horizon ?? 60;
  const windows = [...new Set(wi.map((r) => r.window))].join(", ");
  return (
    <div className="mt-3 grid gap-4 rounded border border-line p-3 md:grid-cols-2" aria-label="Variant results">
      <Bars
        label={`Top ${n} per day: mean ${h}-session return`}
        rows={[{ name: "Frozen score", value: first.baseline_top_per_day_pct, base: true }, ...wi.map((r) => ({ name: variant(r), value: r.what_if?.top_per_day_pct ?? null }))]}
        reference={first.all_mean_pct === null ? undefined : { name: "average of all setups", value: first.all_mean_pct }}
        fmt={(x) => pct(x)}
      />
      <Bars
        label={`Ranking quality: IC with the ${h}-session return (0 = no edge)`}
        rows={[{ name: "Frozen score", value: first.baseline_ic, base: true }, ...wi.map((r) => ({ name: variant(r), value: r.what_if?.ic ?? null }))]}
        fmt={ic}
      />
      <p className="text-[11px] text-mute md:col-span-2">
        {`${windows} window · ${first.setups ?? "–"} setups on ${first.days ?? "–"} scan days. Bars to the right of the dashed line beat the average setup; an IC near 0 means the order does not predict returns.`}
      </p>
    </div>
  );
}

/** A study's groups: mean return per group against the whole. */
export function StudyChart({ run }: { run: AgentRun }) {
  const groups = run.study_groups ?? [];
  if (!run.ok || groups.length === 0) return null;
  return (
    <div className="mt-2 rounded border border-line p-3" aria-label={`Study run ${run.run_id}`}>
      <Bars
        label={`Run ${run.run_id}: mean return by group (${run.window})`}
        rows={groups.slice(0, 12).map((g) => ({ name: `${g.group}${g.n !== null ? ` (${g.n})` : ""}${g.small_sample ? " *" : ""}`, value: g.mean_ret_pct }))}
        reference={run.study_overall?.mean_ret_pct === null || run.study_overall?.mean_ret_pct === undefined ? undefined : { name: "all setups", value: run.study_overall.mean_ret_pct }}
        fmt={(x) => pct(x)}
      />
      {groups.some((g) => g.small_sample) ? <p className="mt-1 text-[11px] text-mute">* small sample: read with care</p> : null}
    </div>
  );
}
