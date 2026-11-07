"use client";

import { DASH, daysBetween, fmtDay, fmtInt, fmtNum, fmtPct, sentence } from "@/lib/fmt";
import type { StockSetup, StockSetups } from "@/lib/schemas";
import { ScoreRing, type Arc } from "./Rings";
import { Card } from "./ui";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1">
      <dt className="text-mute">{k}</dt>
      <dd className="min-w-0 break-words text-right tabular-nums text-ink">{v}</dd>
    </div>
  );
}

export const asNumber = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The rules a setup did not meet for each higher tier (stored as {tier: [rule, ...]}). */
export function unmetByTier(details: Record<string, unknown>): [string, string[]][] {
  const u = details["unmet_rules"];
  if (!u || typeof u !== "object" || Array.isArray(u)) return [];
  return Object.entries(u as Record<string, unknown>)
    .filter(([, rules]) => Array.isArray(rules) && rules.length > 0)
    .map(([tier, rules]) => [tier, (rules as unknown[]).map(String)]);
}

const COMPONENT_COLORS: Record<string, string> = {
  TREND: "#26c281",
  VCP: "#2f6df6",
  VOLUME: "#f5a524",
  RS: "#ef5350",
};
const EXTRA_COLORS = ["#8b5cf6", "#22d3ee", "#f472b6"];

/** Score parts of one setup, summed per component (points and the most they could give). */
export function componentTotals(setup: StockSetup): { component: string; got: number; max: number }[] {
  const by = new Map<string, { got: number; max: number }>();
  for (const p of setup.score_parts) {
    const t = by.get(p.component) ?? { got: 0, max: 0 };
    t.got += p.points ?? 0;
    t.max += p.max_points ?? 0;
    by.set(p.component, t);
  }
  return [...by.entries()].map(([component, t]) => ({ component, ...t }));
}

/** The VCP contractions, each with its drop, its days and whether it is tighter than the one before. */
export function ContractionTable({ setup }: { setup: StockSetup }) {
  return setup.contractions.length > 0 ? (
    <table className="mb-3 w-full text-left text-xs">
      <thead className="bg-panel2 text-mute">
        <tr>
          <th className="px-2 py-1 font-medium">Contraction</th>
          <th className="px-2 py-1 text-right font-medium">Drop %</th>
          <th className="px-2 py-1 text-right font-medium" title="Calendar days from the peak to the trough">
            Days
          </th>
          <th className="px-2 py-1 text-right font-medium">Status</th>
        </tr>
      </thead>
      <tbody>
        {setup.contractions.map((c, i) => {
          const before = setup.contractions[i - 1]?.depth_pct ?? null;
          const tighter = before !== null && c.depth_pct !== null ? c.depth_pct < before : null;
          return (
            <tr key={c.sequence} className="border-t border-line/60">
              <td className="px-2 py-1.5 text-ink">T{c.sequence}</td>
              <td className="px-2 py-1.5 text-right tabular-nums">{fmtPct(c.depth_pct)}</td>
              <td className="px-2 py-1.5 text-right tabular-nums">
                {fmtInt(daysBetween(c.peak_date, c.trough_date))}
              </td>
              <td
                className={`px-2 py-1.5 text-right ${tighter === null ? "text-mute" : tighter ? "text-up" : "text-warn"}`}
              >
                {tighter === null ? "First" : tighter ? "✓ Tighter" : "Wider"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  ) : (
    <p className="mb-3 text-xs text-mute">No contractions stored for this setup.</p>
  );
}

export function TrendCard({ stock }: { stock: StockSetups }) {
  const passed = stock.conditions.filter((c) => c.passed === true).length;
  const all = stock.conditions.length > 0 && passed === stock.conditions.length;
  return (
    <Card
      title="Trend Template"
     
      right={
        <span className={`text-sm font-medium ${all ? "text-up" : "text-mute"}`}>
          {stock.conditions.length ? `${passed} / ${stock.conditions.length}${all ? " ✓" : ""}` : DASH}
        </span>
      }
    >
      {stock.conditions.length === 0 ? (
        <p className="text-xs text-mute">Not scanned on {fmtDay(stock.as_of)}.</p>
      ) : (
        <ul className="text-xs">
          {stock.conditions.map((c) => (
            <li key={c.name} className="flex items-center justify-between gap-2 py-1">
              <span className="flex items-center gap-2">
                <span
                  aria-label={c.passed === null ? "not judged" : c.passed ? "passed" : "failed"}
                  className={`flex h-4 w-4 items-center justify-center rounded text-[10px] ${
                    c.passed === null ? "bg-panel2 text-mute" : c.passed ? "bg-up text-bg" : "bg-down text-bg"
                  }`}
                >
                  {c.passed === null ? "•" : c.passed ? "✓" : "✗"}
                </span>
                <span className="text-ink">{sentence(c.name)}</span>
              </span>
              <span className="tabular-nums text-mute">
                {fmtNum(c.measurement, 2)} vs {fmtNum(c.threshold, 2)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <dl className="mt-3 border-t border-line pt-2 text-xs">
        <Row k="Weekly stage" v={stock.weekly_stage ? sentence(stock.weekly_stage) : DASH} />
        <Row
          k="Stage 2 check"
          v={stock.weekly_stage2_pass === null ? DASH : stock.weekly_stage2_pass ? "Pass" : "Fail"}
        />
      </dl>
    </Card>
  );
}

export function ScoreCard({ setup }: { setup: StockSetup | null }) {
  const totals = setup ? componentTotals(setup) : [];
  let extra = 0;
  const arcs: (Arc & { got: number })[] = totals.map((t) => ({
    label: t.component,
    value: t.got,
    got: t.got,
    max: t.max,
    color: COMPONENT_COLORS[t.component] ?? EXTRA_COLORS[extra++ % EXTRA_COLORS.length] ?? "#8a97b3",
  }));
  return (
    <Card title="Score breakdown">
      {!setup || arcs.length === 0 ? (
        <p className="text-xs text-mute">No score parts stored.</p>
      ) : (
        <div className="flex flex-1 flex-col">
          <div className="flex flex-1 items-center justify-center pb-3">
            <ScoreRing arcs={arcs} center={fmtNum(setup.score, 0)} caption="Final score" />
          </div>
          <ul className="flex-1 space-y-1.5 border-t border-line pt-3 text-xs">
            {arcs.map((a) => (
              <li key={a.label} className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2 text-mute">
                  <span aria-hidden="true" className="h-2 w-2 rounded-sm" style={{ background: a.color }} />
                  {sentence(a.label)}
                </span>
                <span className="tabular-nums text-ink">
                  {fmtNum(a.got)} <span className="text-mute">/ {fmtNum(a.max, 0)}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

