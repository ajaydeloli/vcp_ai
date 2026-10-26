"use client";

// The AI agent's page (AGENT_SPECIFICATION A3 stage 2): its proposals and your decisions, its
// watchlist, the research lab and recent notebook entries. Read-only on purpose: deciding stays
// in your own terminal (`vcp agent approve|reject`), because any program on this PC, the AI's
// shell included, could press a button on a local web page. The page gives the command to copy.
import { useState } from "react";
import { useAgent } from "@/lib/api";
import { fmtDay, fmtStamp } from "@/lib/fmt";
import type { AgentHypothesis, AgentProposal } from "@/lib/schemas";
import { Page } from "./Page";
import { AgentCandidates } from "./AgentCandidates";
import { Pipeline, ReviewCycle, StudyChart, WhatIfCharts } from "./AgentResearch";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const STATUS_STYLE: Record<string, string> = {
  PENDING: "bg-warn/20 text-warn",
  BUILDING: "bg-accent/20 text-accent",
  BUILD_FAILED: "bg-down/20 text-down",
  APPLIED: "bg-up/20 text-up",
  APPROVED: "bg-up/20 text-up",
  REJECTED: "bg-down/20 text-down",
  FAILED: "bg-down/20 text-down",
  OPEN: "bg-accent/20 text-accent",
  CONCLUDED: "bg-line text-mute",
};

function Badge({ status }: { status: string }) {
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_STYLE[status] ?? "bg-line text-mute"}`}>{status}</span>;
}

/** A command to paste into your own terminal, with a copy button. */
function Command({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="flex items-center gap-2">
      <code className="min-w-0 flex-1 truncate rounded border border-line bg-panel2 px-2 py-1 text-[11px] text-ink">{text}</code>
      <button type="button" onClick={copy} aria-label={label} className="shrink-0 rounded border border-line px-2 py-1 text-[11px] text-mute hover:text-ink">
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/** A candidate's change in words (its payload also carries the checks and the scan diff). */
function changeText(p: AgentProposal): string {
  const c = p.change as { overrides?: { path: string; old: unknown; new: unknown }[]; candidate_id?: string } | null;
  if (p.kind !== "candidate" || !c?.overrides) return JSON.stringify(p.change);
  const parts = c.overrides.map((o) => `${o.path}: ${JSON.stringify(o.old)} → ${JSON.stringify(o.new)}`);
  return `${c.candidate_id ?? ""} ${parts.join("; ")}`.trim();
}

function ProposalCard({ p }: { p: AgentProposal }) {
  return (
    <li className="rounded-lg border border-line bg-panel2/40 p-3" aria-label={`Proposal ${p.proposal_id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-ink">{`P${p.proposal_id}`}</span>
        <Badge status={p.status} />
        <span className="text-[11px] text-mute">{p.kind.replace("_", " ")}</span>
        <span className="ml-auto text-[11px] text-mute">{fmtStamp(p.created_at)}</span>
      </div>
      <p className="mt-1 text-sm text-ink">{p.title}</p>
      <dl className="mt-2 grid grid-cols-[6rem_1fr] gap-x-2 gap-y-1 text-xs">
        <dt className="text-mute">Change</dt>
        <dd className="break-all font-mono text-[11px] text-ink">{changeText(p)}</dd>
        <dt className="text-mute">Why</dt>
        <dd className="text-ink">{p.rationale}</dd>
        <dt className="text-mute">Evidence</dt>
        <dd className="break-words text-ink">{p.evidence}</dd>
        {p.decided_by ? (
          <>
            <dt className="text-mute">Decided</dt>
            <dd className="text-ink">{`${p.decided_by}, ${fmtStamp(p.decided_at)}${p.comment ? ` — ${p.comment}` : ""}`}</dd>
          </>
        ) : null}
        {p.result ? (
          <>
            <dt className="text-mute">Result</dt>
            <dd className="text-ink">{p.result}</dd>
          </>
        ) : null}
      </dl>
      {p.approve_command && p.reject_command ? (
        <div className="mt-3 space-y-1.5">
          <p className="text-[11px] text-mute">Decide in your own Ubuntu terminal (you type the number again to confirm):</p>
          <Command text={p.approve_command} label={`Copy the approve command for P${p.proposal_id}`} />
          <Command text={p.reject_command} label={`Copy the reject command for P${p.proposal_id}`} />
        </div>
      ) : null}
    </li>
  );
}

function HypothesisCard({ h }: { h: AgentHypothesis }) {
  return (
    <li className="rounded-lg border border-line bg-panel2/40 p-3" aria-label={`Hypothesis ${h.hypothesis_id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-ink">{`H${h.hypothesis_id}`}</span>
        <Badge status={h.status} />
        {h.outcome ? <span className="text-[11px] font-semibold text-ink">{h.outcome}</span> : null}
        <span className="text-[11px] text-mute">{h.strategy}</span>
        <span className="ml-auto text-[11px] text-mute">{fmtDay(h.created_at.slice(0, 10))}</span>
      </div>
      <p className="mt-1 text-sm text-ink">{h.title}</p>
      <Pipeline h={h} />
      <p className="mt-1 text-xs text-mute">
        <span className="text-ink">Decision rule: </span>
        {h.decision_rule}
      </p>
      {h.runs.length ? (
        <ul className="mt-2 space-y-1 text-[11px]">
          {h.runs.map((r) => (
            <li key={r.run_id} className="text-mute">
              <span className="text-ink">{`run ${r.run_id} · ${r.kind} · ${r.window}`}</span>
              {r.ok === false ? <span className="text-down">{` · failed: ${r.error ?? ""}`}</span> : null}
              {r.summary ? <span className="block break-words">{r.summary}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      <WhatIfCharts runs={h.runs} />
      {h.runs.map((r) => (r.kind === "study" ? <StudyChart key={`s${r.run_id}`} run={r} /> : null))}
      {h.runs.some((r) => r.backtest_id) ? (
        <p className="mt-2 text-[11px] text-mute">
          Backtests of this hypothesis are on the{" "}
          <a href="/backtest" className="text-accent hover:underline">
            Backtest
          </a>{" "}
          page: {h.runs.filter((r) => r.backtest_id).map((r) => r.backtest_id).join(", ")}.
        </p>
      ) : null}
      {h.conclusion ? (
        <p className="mt-2 text-xs text-ink">
          <span className="text-mute">Conclusion: </span>
          {h.conclusion}
        </p>
      ) : null}
    </li>
  );
}

export function AgentPage() {
  const q = useAgent();
  const [show, setShow] = useState<"open" | "all">("open");
  const d = q.data;
  const proposals = (d?.proposals ?? []).filter((p) => show === "all" || p.status === "PENDING" || p.status === "BUILDING");
  return (
    <Page active="AI Agent" title="AI agent">
      {q.isLoading ? <Loading what="the agent" /> : null}
      {q.error ? <ErrorBox error={q.error} /> : null}
      {d && !d.available ? (
        <Card title="AI agent">
          <Empty>{d.reason ?? "Nothing yet."}</Empty>
        </Card>
      ) : null}
      {d && d.available ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <Card
            title="Proposals"
            subtitle={`${d.pending} waiting for you. The AI proposes; only you decide, in your own terminal.`}
            right={
              <span className="flex gap-1 text-[11px]" role="group" aria-label="Which proposals">
                {(["open", "all"] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    aria-pressed={show === k}
                    onClick={() => setShow(k)}
                    className={`rounded px-2 py-0.5 ${show === k ? "bg-line text-ink" : "text-mute hover:text-ink"}`}
                  >
                    {k === "open" ? "Waiting" : "All"}
                  </button>
                ))}
              </span>
            }
          >
            {proposals.length === 0 ? (
              <Empty>{show === "open" ? "Nothing is waiting for a decision." : "No proposals yet."}</Empty>
            ) : (
              <ul className="space-y-3">
                {proposals.map((p) => (
                  <ProposalCard key={p.proposal_id} p={p} />
                ))}
              </ul>
            )}
          </Card>
          <div className="lg:col-span-2 lg:order-last">
            <AgentCandidates items={d.candidates ?? []} minTrades={d.review?.promotion_min_trades ?? 30} minMonths={d.review?.promotion_min_months ?? 3} />
          </div>
          <div className="space-y-4">
            <Card title="AI watchlist" subtitle="Pre-approved: the AI adds and removes symbols itself. Your own lists are on the Watchlist page.">
              {d.ai_watchlist.length === 0 ? (
                <Empty>Empty.</Empty>
              ) : (
                <ul className="flex flex-wrap gap-1.5">
                  {d.ai_watchlist.map((w) => (
                    <li key={w.symbol}>
                      <a href={`/stocks/${encodeURIComponent(w.symbol)}`} title={w.reason} className="rounded border border-line px-2 py-0.5 text-xs text-ink hover:border-accent">
                        {w.symbol}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card
              title="Research lab"
              subtitle={`Hypotheses left before the April 2027 review: ${Object.entries(d.budget_left)
                .map(([s, n]) => `${s.replace(/_/g, " ")} ${n}`)
                .join(" · ")}`}
            >
              <ReviewCycle d={d} />
              {d.hypotheses.length === 0 ? (
                <Empty>No hypothesis registered yet.</Empty>
              ) : (
                <ul className="space-y-3">
                  {d.hypotheses.map((h) => (
                    <HypothesisCard key={h.hypothesis_id} h={h} />
                  ))}
                </ul>
              )}
            </Card>
            <Card title="Notebook" subtitle="Recent decisions and findings">
              {d.notes.length === 0 ? (
                <Empty>Empty.</Empty>
              ) : (
                <ul className="space-y-2 text-xs">
                  {d.notes.map((n) => (
                    <li key={n.note_id}>
                      <span className="text-mute">{`${fmtDay(n.created_at.slice(0, 10))} · ${n.kind} · ${n.source} — `}</span>
                      <span className="text-ink">{n.text}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      ) : null}
    </Page>
  );
}
