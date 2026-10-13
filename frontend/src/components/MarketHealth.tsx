"use client";

import { useMemo } from "react";
import { useMarket, useMarketHealth, usePaper } from "@/lib/api";
import { fmtDay, fmtInt, fmtPct, strategyLabel } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { SetupCounts } from "./SetupCounts";
import { MiniLineChart } from "./MiniLineChart";
import { SetupFlowCard } from "./MarketFlow";
import { HealthScoreCard } from "./HealthScore";
import { BreadthCard, IndexTrendCard, LeadershipCard, ParticipationCard, VolumeCard } from "./HealthCards";
import { SectorRotationCard } from "./SectorRotation";
import { NewHighsCard } from "./NewHighs";
import { SectorCard } from "./SectorStrength";
import { BOX, C, Figure, Inner, Legend } from "./MarketParts";
import { Empty, ErrorBox, Loading } from "./ui";

const DOT: Record<string, string> = {
  green: "bg-up",
  amber: "bg-warn",
  red: "bg-down",
  grey: "bg-mute/60",
};

/** How the open paper positions are doing (their count and the progress toward the review are in
 *  the Paper trading panel). */
function OpenPositions() {
  const paper = usePaper();
  const p = paper.data;
  if (!p) return null;
  const open = p.strategies.flatMap((s) => s.open.map((o) => ({ ...o, strategy: s.strategy_id })));
  const max = Math.max(...open.map((o) => Math.abs(o.open_pct ?? 0)), 1);
  return (
    <div className="mt-4 space-y-4 text-xs">
      <div data-testid="mini-open-positions">
        <p className="mb-2 text-mute">Open paper positions now (gain or loss since entry)</p>
        {open.length === 0 ? (
          <p className="text-mute">No open positions.</p>
        ) : (
          <ul className="space-y-1.5">
            {open.map((o) => {
              const v = o.open_pct ?? 0;
              return (
                <li key={`${o.strategy}:${o.instrument_id}`} className="grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-2">
                  <span className="truncate text-ink">
                    {o.symbol} <span className="text-mute">· {strategyLabel(o.strategy)}</span>
                  </span>
                  <span className="relative h-2 rounded bg-bg">
                    <span className="absolute inset-y-0 left-1/2 w-px bg-line" />
                    <span className={`absolute inset-y-0 ${v >= 0 ? "left-1/2 bg-up" : "right-1/2 bg-down"}`} style={{ width: `${(Math.abs(v) / max) * 50}%` }} />
                  </span>
                  <span className={`text-right tabular-nums ${v >= 0 ? "text-up" : "text-down"}`}>{o.open_pct === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function Trades({ trades }: { trades: Health["trades"] }) {
  if (trades.length === 0) return <p className="text-xs text-mute">No closed paper trades yet.</p>;
  const max = Math.max(...trades.map((t) => Math.abs(t.ret_pct)), 1);
  return (
    <div role="img" aria-label="trades-chart" data-testid="mini-trades-chart">
      <div className="flex h-24 items-center gap-1">
        {trades.map((t, i) => (
          <div key={i} className="flex h-full flex-1 flex-col justify-center" title={`${fmtDay(t.day)}: ${t.ret_pct.toFixed(1)}%`}>
            <div className="flex h-1/2 items-end">{t.ret_pct > 0 && <div className="w-full bg-up" style={{ height: `${(t.ret_pct / max) * 100}%` }} />}</div>
            <div className="flex h-1/2 items-start">{t.ret_pct <= 0 && <div className="w-full bg-down" style={{ height: `${(-t.ret_pct / max) * 100}%` }} />}</div>
          </div>
        ))}
      </div>
      <p className="mt-1 text-[11px] text-mute">Return of each of the last {trades.length} closed paper trades, oldest first.</p>
    </div>
  );
}

/** The readings as one line each: dot, name, the figure, its 0 to 100 score. The sentence is the tooltip. */
function Readings({ group }: { group: Health["groups"][number] }) {
  return (
    <>
      <ul className="mt-3 divide-y divide-line/60 text-xs">
        {group.items.map((i) => (
          <li key={i.id} className="flex items-center gap-2 py-1.5" title={i.text}>
            <span role="img" aria-label={i.status} className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[i.status] ?? DOT.grey}`} />
            <span className="min-w-0 flex-1 truncate text-ink">{i.label}</span>
            <span className="shrink-0 text-right text-mute">{i.short}</span>
            <span className="w-7 shrink-0 text-right tabular-nums text-ink" title="score out of 100">
              {i.score === null ? "" : Math.round(i.score)}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

/** The strategies' frozen regime rule (not a health reading): on when at least the threshold
 *  of the scan's universe closes above its 50-day average. */
function StrategyRegime() {
  const market = useMarket(250);
  const m = market.data;
  const last = m?.days[m.days.length - 1];
  return (
    <div
      className="border-t border-line pt-2"
      title={
        m
          ? `The strategies' regime is ${m.regime_rule}: on when at least ${m.breadth_threshold_pct}% of the scan's universe closes above its 50-day average. A frozen rule, not part of the health score.`
          : undefined
      }
    >
      <Figure
        small
        label="Strategy regime (frozen rule)"
        value={last ? (last.regime_on ? "ON" : "OFF") : "—"}
        sub={m ? `(${m.regime_days_on_last_20}/20 days on)` : undefined}
        color={last ? (last.regime_on ? "text-up" : "text-down") : "text-ink"}
      />
    </div>
  );
}

const STAGES = [
  { key: "stage1", label: "Stage 1: base", color: C.blue, text: "text-accent" },
  { key: "stage2", label: "Stage 2: uptrend", color: C.up, text: "text-up" },
  { key: "stage3", label: "Stage 3: top", color: C.warn, text: "text-warn" },
  {
    key: "stage4",
    label: "Stage 4: downtrend",
    color: C.down,
    text: "text-down",
  },
] as const;

/** The scanned stocks by weekly stage: the share in each of the four stages week by week, and today's shares. */
function StageCard({ stages, box }: { stages: Health["stages"]; box: string }) {
  const lines = useMemo(
    () =>
      STAGES.map((st) => ({
        name: st.label,
        color: st.color,
        data: stages.filter((p) => p.total > 0).map((p) => ({ time: p.day, value: (p[st.key] / p.total) * 100 })),
      })),
    [stages],
  );
  const last = stages[stages.length - 1];
  return (
    <Inner title="Market stage" box={box}>
      {!last || last.total === 0 ? (
        <Empty>No stage history to show yet.</Empty>
      ) : (
        <>
          <div className="flex gap-4">
            <div className="min-w-0 flex-1">
              <MiniLineChart series={lines} height={262} plain label="stage-chart" />
              <Legend items={STAGES.map((st) => [st.label, st.color])} />
            </div>
            <aside aria-label="Stage figures" className="w-44 shrink-0 space-y-2 border-l border-line pl-4">
              {STAGES.map((st, i) => (
                <div key={st.key} className={i ? "border-t border-line pt-2" : ""}>
                  <Figure label={st.label} value={fmtPct((last[st.key] / last.total) * 100, 0)} sub={`(${fmtInt(last[st.key])} stocks)`} color={st.text} small />
                </div>
              ))}
            </aside>
          </div>
        </>
      )}
    </Inner>
  );
}

/** The feedback loop: our paper trades (not part of the score). */
function Feedback({ d }: { d: Health }) {
  return (
    <>
      <Trades trades={d.trades} />
      <OpenPositions />
    </>
  );
}

function Warnings({ warnings }: { warnings: Health["warnings"] }) {
  if (warnings.length === 0) return null;
  return (
    <ul aria-label="Divergence warnings" className="mb-4 space-y-1 rounded-lg border border-warn/50 bg-warn/10 p-3 text-xs text-ink">
      {warnings.map((w) => (
        <li key={w.id} className="flex gap-2">
          <span aria-hidden="true" className="text-warn">
            ⚠
          </span>
          <span>
            <span className="font-medium text-warn">Divergence: </span>
            {w.text}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The market read the way Minervini reads it: price action, leadership, breadth, our own trades, stages, setups. */
export function MarketHealth() {
  const health = useMarketHealth();
  const d = health.data;
  const fb = d?.groups.find((g) => g.id === "feedback");
  const pillar = (id: string) => d?.score?.pillars.find((p) => p.id === id);
  return (
    <section className="min-w-0" aria-label="Market health overview">
      {health.isError ? (
        <ErrorBox error={health.error} />
      ) : !d ? (
        <Loading what="market health" />
      ) : d.as_of === null ? (
        <Empty>No market data to read yet.</Empty>
      ) : (
        <>
          <Warnings warnings={d.warnings} />
          <div className="grid gap-4 lg:grid-cols-2">
            <HealthScoreCard score={d.score} />
            <IndexTrendCard pts={d.score_points} pillar={pillar("index_trend")} />
            <BreadthCard pts={d.score_points} pillar={pillar("breadth")} regime={<StrategyRegime />} />
            <VolumeCard pts={d.score_points} pillar={pillar("accumulation")} />
            <ParticipationCard pts={d.score_points} pillar={pillar("breadth")} lastThrust={d.score?.last_thrust} />
            <LeadershipCard pts={d.score_points} pillar={pillar("leadership")} />
            <NewHighsCard highs={d.new_highs} box={BOX.highs} />
            <StageCard stages={d.stages} box={BOX.stage} />
            <SetupFlowCard flow={d.flow} box={BOX.flow} pillar={pillar("breakouts")} judged={d.score?.judged} />
            <SectorCard sectors={d.sectors} box={`${BOX.sectors} lg:col-span-2`} reading={pillar("leadership")?.parts.find((x) => x.id === "sectors")} />
            <SectorRotationCard rotation={d.rotation} />
            {fb && (
              <Inner title={fb.title} box={BOX.feedback}>
                <Feedback d={d} />
                <Readings group={fb} />
              </Inner>
            )}
            <SetupCounts box={BOX.setups} />
          </div>
        </>
      )}
    </section>
  );
}
