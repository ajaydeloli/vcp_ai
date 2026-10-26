"use client";

// Strategy candidates on the AI Agent page (AGENT_SPECIFICATION step A4): each candidate's
// changes, the scan diff against the frozen scan on the same days, and, once you approved it,
// its shadow paper ledger against the frozen one. Read-only: approving stays in your terminal.
import { useState } from "react";
import { DASH, fmtDay, fmtNum, fmtPct, strategyLabel } from "@/lib/fmt";
import type { AgentCandidate } from "@/lib/schemas";
import { Card, Empty } from "./ui";

const CHANGE_LABEL: Record<string, string> = {
  ADDED: "Added",
  DROPPED: "Dropped",
  RECLASSIFIED: "Reclassified",
  RESCORED: "Re-scored",
};

const STATUS_STYLE: Record<string, string> = {
  active: "bg-up/20 text-up",
  proposed: "bg-warn/20 text-warn",
  rejected: "bg-down/20 text-down",
  retired: "bg-line text-mute",
};

const show = (v: unknown): string => (v === null || v === undefined ? DASH : typeof v === "string" ? v : JSON.stringify(v));
const cls = (c: string | null): string => (c ? c.toLowerCase().replace(/_/g, " ") : DASH);

function ShadowTable({ c }: { c: AgentCandidate }) {
  const s = c.shadow;
  if (!s) {
    return (
      <p className="text-xs text-mute">
        {c.shadow_error
          ? `Shadow comparison unavailable: ${c.shadow_error}`
          : c.status === "active"
            ? "No shadow trades yet."
            : "Not started: only an approved candidate runs in the shadow ledger."}
      </p>
    );
  }
  const rows = [
    ["Frozen", s.frozen],
    ["Candidate", s.candidate],
  ] as const;
  return (
    <div className="space-y-1">
      <table className="w-full text-xs" aria-label={`Shadow ledger of ${c.candidate_id}`}>
        <thead className="text-mute">
          <tr>
            <th className="text-left font-normal">{`Since ${fmtDay(s.start_date)} (${s.days} days)`}</th>
            <th className="text-right font-normal">Closed</th>
            <th className="text-right font-normal">Open</th>
            <th className="text-right font-normal">Win</th>
            <th className="text-right font-normal">Avg</th>
            <th className="text-right font-normal">PF</th>
            <th className="text-right font-normal">Total</th>
            <th className="text-right font-normal">Max DD</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, x]) => (
            <tr key={name} className="text-ink">
              <td>{name}</td>
              <td className="text-right">{x.closed}</td>
              <td className="text-right">{x.open}</td>
              <td className="text-right">{fmtPct(x.win_rate, 0)}</td>
              <td className="text-right">{fmtPct(x.avg_ret, 2, true)}</td>
              <td className="text-right">{fmtNum(x.profit_factor, 2)}</td>
              <td className="text-right">{fmtPct(x.total_ret, 1, true)}</td>
              <td className="text-right">{fmtPct(-x.max_drawdown, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-mute">
        {s.avg_ret_diff === null
          ? "Difference per trade: shown once each side has 5 closed trades."
          : `Candidate minus frozen: ${fmtPct(s.avg_ret_diff, 2, true)} per trade (95% range ${fmtPct(s.avg_ret_diff_lo, 2, true)} to ${fmtPct(s.avg_ret_diff_hi, 2, true)}).`}{" "}
        <span className={s.ready_to_judge ? "text-up" : ""}>{s.ready_to_judge ? "Enough to judge at the review." : `Needs ${s.needs.join(" and ")}.`}</span>
      </p>
    </div>
  );
}

function CandidateCard({ c }: { c: AgentCandidate }) {
  const [open, setOpen] = useState(false);
  const d = c.scan_diff;
  return (
    <li className="rounded-lg border border-line bg-panel2/40 p-3" aria-label={`Candidate ${c.candidate_id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-ink">{c.candidate_id}</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_STYLE[c.status] ?? "bg-line text-mute"}`}>{c.status}</span>
        <span className="text-[11px] text-mute">{`${strategyLabel(c.strategy_id)} · ${c.candidate_version}${c.hypothesis_id ? ` · H${c.hypothesis_id}` : ""} · P${c.proposal_id}`}</span>
        {c.start_date ? <span className="ml-auto text-[11px] text-mute">{`Shadow from ${fmtDay(c.start_date)}`}</span> : null}
      </div>
      <ul className="mt-2 space-y-0.5 text-xs">
        {c.changes.map((o) => (
          <li key={`${o.file}:${o.path}`} className="text-ink">
            <span className="text-mute">{`${o.file} `}</span>
            {`${o.path}: ${show(o.old)} → ${show(o.new)}`}
          </li>
        ))}
      </ul>
      <div className="mt-2 text-xs">
        <span className="text-mute">Scan diff: </span>
        {!d ? (
          <span className="text-mute">not run</span>
        ) : d.paper_rules_only ? (
          <span className="text-ink">{d.note ?? "paper rules only; the scan is unchanged"}</span>
        ) : (
          <span className="text-ink">
            {`${d.days ?? 0} sessions ${d.first ? fmtDay(d.first) : ""}–${d.last ? fmtDay(d.last) : ""}, ${d.frozen_eligible ?? 0} frozen setups: `}
            {Object.entries(CHANGE_LABEL)
              .map(([k, label]) => `${label} ${d.changes[k] ?? 0}`)
              .join(" · ")}
          </span>
        )}
        {c.diff_rows.length ? (
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="ml-2 text-accent hover:underline">
            {open ? "Hide setups" : "Show setups"}
          </button>
        ) : null}
      </div>
      {open ? (
        <table className="mt-1 w-full text-xs" aria-label={`Scan diff of ${c.candidate_id}`}>
          <thead className="text-mute">
            <tr>
              <th className="text-left font-normal">Day</th>
              <th className="text-left font-normal">Stock</th>
              <th className="text-left font-normal">Change</th>
              <th className="text-left font-normal">Frozen</th>
              <th className="text-left font-normal">Candidate</th>
            </tr>
          </thead>
          <tbody>
            {c.diff_rows.map((r) => (
              <tr key={`${r.date}:${r.symbol}`} className="text-ink">
                <td>{fmtDay(r.date)}</td>
                <td>
                  <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="hover:text-accent">
                    {r.symbol}
                  </a>
                </td>
                <td>{CHANGE_LABEL[r.change] ?? r.change}</td>
                <td>{`${cls(r.frozen_class)} ${r.frozen_score === null ? "" : fmtNum(r.frozen_score, 1)}`}</td>
                <td>{`${cls(r.candidate_class)} ${r.candidate_score === null ? "" : fmtNum(r.candidate_score, 1)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <div className="mt-2">
        <ShadowTable c={c} />
      </div>
    </li>
  );
}

export function AgentCandidates({ items, minTrades, minMonths }: { items: AgentCandidate[]; minTrades: number; minMonths: number }) {
  return (
    <Card
      title="Strategy candidates"
      subtitle={`One idea each, run in a shadow ledger beside the frozen strategy. Promotion needs ${minTrades} shadow trades and ${minMonths} months, and is yours at the review.`}
    >
      {items.length === 0 ? (
        <Empty>No candidate yet. The AI proposes one from a registered hypothesis; you approve it in your own terminal.</Empty>
      ) : (
        <ul className="space-y-3">
          {items.map((c) => (
            <CandidateCard key={c.candidate_id} c={c} />
          ))}
        </ul>
      )}
    </Card>
  );
}
