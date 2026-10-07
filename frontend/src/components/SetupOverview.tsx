"use client";

import { useSetups, useStockSetups } from "@/lib/api";
import { DASH, daysBetween, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, sentence } from "@/lib/fmt";
import type { StockSetup, StockSetups } from "@/lib/schemas";
import { ScoreRing, type Arc } from "./Rings";
import type { Selection } from "./SetupsTable";
import { Card, ErrorBox, Loading } from "./ui";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1">
      <dt className="text-mute">{k}</dt>
      <dd className="min-w-0 break-words text-right tabular-nums text-ink">{v}</dd>
    </div>
  );
}

const asNumber = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

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

function PatternCard({
  stock,
  setup,
  baseDays,
  distance,
}: {
  stock: StockSetups;
  setup: StockSetup | null;
  baseDays: number | null;
  distance: number | null;
}) {
  const d = setup?.details ?? {};
  const unmet = setup ? unmetByTier(setup.details) : [];
  return (
    <Card title="VCP pattern" subtitle={setup ? undefined : `No setup stored for this stock on ${fmtDay(stock.as_of)}.`}>
      {setup ? (
        <>
          {setup.contractions.length > 0 ? (
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
          )}
          <dl className="text-xs">
            <Row k="Base depth" v={fmtPct(setup.base_depth_pct)} />
            <Row k="Base duration" v={baseDays === null ? DASH : `${baseDays} days`} />
            <Row k="Pivot price" v={fmtPrice(setup.pivot)} />
            <Row k="Current price" v={fmtPrice(stock.close)} />
            <Row k="Distance to pivot" v={fmtPct(distance)} />
            <Row k="Stop" v={fmtPrice(setup.stop)} />
            <Row k="Base start" v={fmtDay(setup.base_start)} />
            <Row k="Base high / low" v={`${fmtPrice(asNumber(d["base_high"]))} / ${fmtPrice(asNumber(d["base_low"]))}`} />
            <Row k="Prior advance" v={fmtPct(asNumber(d["prior_advance_pct"]))} />
            <Row k="Volume dry-up" v={fmtNum(asNumber(d["dryup_volume_ratio"]), 2)} />
            <Row k="Breakout" v={setup.breakout_date ? fmtDay(setup.breakout_date) : DASH} />
          </dl>
          {unmet.map(([tier, rules]) => (
            <p key={tier} className="mt-2 text-xs text-warn">
              Not {sentence(tier)}: {rules.map(sentence).join(", ")}
            </p>
          ))}
        </>
      ) : null}
    </Card>
  );
}

function TrendCard({ stock }: { stock: StockSetups }) {
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

const FUNDAMENTALS = ["EPS (TTM)", "Sales (TTM)", "ROE", "ROCE", "Debt / Equity", "Operating margin", "Net profit margin"];

function FundamentalsCard() {
  return (
    <Card title="Fundamentals" right={<span className="text-[11px] text-mute">Not available</span>}>
      <dl className="text-xs">
        {FUNDAMENTALS.map((k) => (
          <Row key={k} k={k} v={DASH} />
        ))}
      </dl>
      <p className="mt-3 text-[11px] text-mute">
        Company fundamentals are not stored yet, so no values are shown (—, not 0).
      </p>
    </Card>
  );
}

function ScoreCard({ setup }: { setup: StockSetup | null }) {
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
        <div className="flex flex-wrap items-center gap-4">
          <ScoreRing arcs={arcs} center={fmtNum(setup.score, 0)} caption="Final score" />
          <ul className="min-w-0 flex-1 space-y-1.5 text-xs">
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

export function SetupOverview({ selection }: { selection: Selection | null }) {
  const stock = useStockSetups(selection?.symbol ?? null);
  const ranked = useSetups(selection?.strategy ?? "vcp", undefined, selection !== null);
  if (!selection) return null;
  if (stock.isError) return <ErrorBox error={stock.error} />;
  if (stock.isPending) return <Loading what="setup overview" />;

  const setups = stock.data.setups;
  const setup = setups.find((s) => s.strategy_id === selection.strategy) ?? setups[0] ?? null;
  const row = ranked.data?.rows.find((r) => r.symbol === selection.symbol && r.strategy_id === setup?.strategy_id);

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-[1.15fr_1fr_0.85fr_1fr]">
      <PatternCard stock={stock.data} setup={setup} baseDays={row?.base_days ?? null} distance={row?.pivot_distance_pct ?? null} />
      <TrendCard stock={stock.data} />
      <FundamentalsCard />
      <ScoreCard setup={setup} />
    </div>
  );
}
