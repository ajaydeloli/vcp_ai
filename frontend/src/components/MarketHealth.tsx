"use client";

import { type ReactNode, useMemo } from "react";
import { useMarket, useMarketHealth, usePaper } from "@/lib/api";
import { fmtDay, fmtInt, fmtPct, strategyLabel } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { SetupCounts } from "./SetupCounts";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { SetupFlowCard } from "./MarketFlow";
import { HealthScoreCard } from "./HealthScore";
import { ScoreReadings } from "./ScoreReadings";
import { SectorRotationCard } from "./SectorRotation";
import { SectorCard } from "./SectorStrength";
import { BOX, C, Figure, Inner, Legend, NSE } from "./MarketParts";
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
        label="Strategy regime (frozen rule)"
        value={last ? (last.regime_on ? "ON" : "OFF") : "—"}
        sub={m ? `(${m.regime_days_on_last_20}/20 days on)` : undefined}
        color={last ? (last.regime_on ? "text-up" : "text-down") : "text-ink"}
      />
    </div>
  );
}

function FigureColumn({ children, label }: { children: ReactNode; label: string }) {
  return (
    <aside aria-label={label} className="w-56 shrink-0 space-y-3 border-l border-line pl-4">
      {children}
    </aside>
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
            <aside aria-label="Stage figures" className="w-40 shrink-0 space-y-3 border-l border-line pl-4">
              {STAGES.map((st, i) => (
                <div key={st.key} className={i ? "border-t border-line pt-3" : ""}>
                  <Figure label={st.label} value={fmtPct((last[st.key] / last.total) * 100, 0)} sub={`(${fmtInt(last[st.key])} stocks)`} color={st.text} />
                </div>
              ))}
            </aside>
          </div>
        </>
      )}
    </Inner>
  );
}

type SP = Health["score_points"][number];

function line(name: string, color: string, pts: SP[], pick: (p: SP) => number | null): MiniSeries {
  const data: { time: string; value: number }[] = [];
  for (const p of pts) {
    const v = pick(p);
    if (v !== null) data.push({ time: p.day, value: v });
  }
  return { name, color, data };
}

const SMALLCAP = "#f472b6";

/** The cards' charts: the Market Health Score's own readings per session (FRONTEND_SPECIFICATION
 *  §67.34), so a chart never tells a different story from the score. The feedback loop is our
 *  paper trades (not part of the score). */
function Charts({ d, id }: { d: Health; id: string }) {
  const pts = d.score_points;
  const index = useMemo(
    () => [
      line("NIFTY 500", NSE.nifty500, pts, (p) => p.nifty500),
      line("50-day", C.warn, pts, (p) => p.n500_ma50),
      line("150-day", C.violet, pts, (p) => p.n500_ma150),
      line("200-day", C.down, pts, (p) => p.n500_ma200),
      line("NIFTY 50", NSE.nifty50, pts, (p) => p.nifty50),
      line("Smallcap 250", SMALLCAP, pts, (p) => p.smallcap250),
      // accumulation (green) and distribution days (red) of NIFTY 500 with its own volume, as
      // small dots on one line above the dates (the score's rule)
      {
        name: "Volume days",
        color: C.up,
        kind: "dots" as const,
        data: pts.flatMap((p) =>
          p.kind === "accumulation" ? [{ time: p.day, value: 0, color: C.up }] : p.kind === "distribution" ? [{ time: p.day, value: 0, color: C.down }] : [],
        ),
      },
    ],
    [pts],
  );
  const highs = useMemo(() => [line("Highs share %", C.blue, pts, (p) => p.highs_share)], [pts]);
  const tt = useMemo(() => [line("Trend Template %", C.up, pts, (p) => p.tt_share)], [pts]);
  const breadth = useMemo(
    () => [
      line("Above 50-day %", C.blue, pts, (p) => p.above50),
      line("Above 150-day %", C.warn, pts, (p) => p.above150),
      line("Above 200-day %", C.violet, pts, (p) => p.above200),
    ],
    [pts],
  );
  const net = useMemo(() => [line("Net advances %", C.ink, pts, (p) => (p.ad_ratio === null ? null : 100 * p.ad_ratio))], [pts]);
  const gap = useMemo(() => [line("VQI", C.ink, pts, (p) => p.vqi), line("NIFTY 500", NSE.nifty500, pts, (p) => p.nifty500)], [pts]);
  const thrust = useMemo(
    () => [
      {
        ...line("Breadth thrust", C.blue, pts, (p) => p.thrust_ratio),
        markers: pts
          .filter((p) => p.thrust)
          .map((p) => ({
            time: p.day,
            color: C.up,
            position: "aboveBar" as const,
            text: "thrust",
          })),
      },
    ],
    [pts],
  );
  const lastThrust = [...pts].reverse().find((p) => p.thrust);
  if (id === "index")
    return (
      <>
        <MiniLineChart series={index} height={262} plain label="index-price-chart" />
        <Legend
          items={[
            ["NIFTY 500", NSE.nifty500],
            ["its 50-day", C.warn],
            ["its 150-day", C.violet],
            ["its 200-day", C.down],
            ["NIFTY 50", NSE.nifty50],
            ["Smallcap 250", SMALLCAP],
            ["● accumulation day", C.up],
            ["● distribution day", C.down],
          ]}
        />
        <p className="text-[11px] text-mute">All lines start at 100. Dots: NIFTY 500 up or down 0.2% or more on higher volume than the day before.</p>
      </>
    );
  if (id === "leadership")
    return (
      <>
        <MiniLineChart
          series={highs}
          height={110}
          plain
          threshold={[
            { value: 20, title: "20" },
            { value: 80, title: "80" },
          ]}
          label="highs-lows-chart"
        />
        <Legend
          items={[
            ["New 52-week highs / (highs + lows), 10-day averages, %", C.blue],
            ["dashed: 20% and 80%, where it scores 0 and 100", C.warn],
          ]}
        />
        <MiniLineChart
          series={tt}
          height={90}
          plain
          threshold={[
            { value: 3, title: "3" },
            { value: 20, title: "20" },
          ]}
          label="trend-template-chart"
        />
        <Legend
          items={[
            ["Stocks passing the Trend Template, %", C.up],
            ["dashed: 3% and 20%", C.warn],
          ]}
        />
        <NewHighsList highs={d.new_highs} />
      </>
    );
  if (id === "breadth")
    return (
      <>
        <MiniLineChart
          series={breadth}
          height={88}
          plain
          threshold={[
            { value: 30, title: "30%" },
            { value: 70, title: "70%" },
          ]}
          label="breadth-chart"
        />
        <Legend
          items={[
            ["% above 50-day", C.blue],
            ["% above 150-day", C.warn],
            ["% above 200-day", C.violet],
            ["dashed: 30% and 70%", C.warn],
          ]}
        />
        <MiniLineChart
          series={net}
          height={64}
          plain
          threshold={[
            { value: -15, title: "-15" },
            { value: 15, title: "15" },
          ]}
          label="ad-line-chart"
        />
        <Legend
          items={[
            ["Net advances over 20 sessions, % of all moves", C.ink],
            ["dashed: ±15%", C.warn],
          ]}
        />
        <MiniLineChart series={gap} height={64} plain label="vqi-gap-chart" />
        <Legend
          items={[
            ["VQI (the average stock)", C.ink],
            ["NIFTY 500", NSE.nifty500],
          ]}
        />
        <MiniLineChart
          series={thrust}
          height={64}
          plain
          threshold={[
            { value: 40, title: "40%" },
            { value: 61.5, title: "61.5%" },
          ]}
          label="thrust-chart"
        />
        <Legend
          items={[
            ["Breadth thrust: 10-day average of advancers / (advancers + decliners), %", C.blue],
            ["dashed: 40% and 61.5%", C.warn],
          ]}
        />
        <p className="text-[11px] text-mute" data-testid="thrust-note">
          {lastThrust
            ? `Last thrust ${fmtDay(lastThrust.day)}: from below 40% to above 61.5% within 10 sessions, a classic end-of-correction signal.`
            : "No breadth thrust in this chart (from below 40% to above 61.5% within 10 sessions)."}
        </p>
      </>
    );
  return (
    <>
      <Trades trades={d.trades} />
      <OpenPositions />
    </>
  );
}

/** The universe's stocks at a new 52-week high today, best RS first, each a link to its page. */
function NewHighsList({ highs }: { highs: Health["new_highs"] }) {
  return (
    <div className="mt-2" data-testid="new-highs-today">
      <p className="mb-1 text-[11px] uppercase text-mute">New highs today ({highs.length})</p>
      {highs.length === 0 ? (
        <p className="text-[11px] text-mute">No stock of the universe made a new 52-week high today.</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5 text-[11px]">
          {highs.map((h) => (
            <li key={h.symbol}>
              <a
                href={`/stocks/${encodeURIComponent(h.symbol)}`}
                title={h.sector ?? "Sector not classified yet"}
                className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-ink hover:border-accent"
              >
                {h.symbol}
                <span className="text-mute">RS {h.rs_rank ?? "—"}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Divergences, when the index is near its 52-week high but breadth is not: a warning, not a score. */
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
            {
              <Inner title="Index trend" box={BOX.index}>
                <div className="flex gap-4">
                  <div className="min-w-0 flex-1">
                    <Charts d={d} id="index" />
                  </div>
                  <FigureColumn label="Index figures">
                    <ScoreReadings pillar={pillar("index_trend")} />
                    <ScoreReadings pillar={pillar("accumulation")} />
                  </FigureColumn>
                </div>
              </Inner>
            }
            {
              <Inner title="Leadership" box={BOX.leadership}>
                <div className="flex gap-4">
                  <div className="min-w-0 flex-1">
                    <Charts d={d} id="leadership" />
                  </div>
                  <FigureColumn label="Leadership figures">
                    <ScoreReadings pillar={pillar("leadership")} />
                  </FigureColumn>
                </div>
              </Inner>
            }
            {
              <Inner title="Breadth" box={BOX.breadth}>
                <div className="flex gap-4">
                  <div className="min-w-0 flex-1">
                    <Charts d={d} id="breadth" />
                  </div>
                  <FigureColumn label="Breadth figures">
                    <ScoreReadings pillar={pillar("breadth")} />
                    <StrategyRegime />
                  </FigureColumn>
                </div>
              </Inner>
            }
            <StageCard stages={d.stages} box={BOX.stage} />
            <SetupFlowCard flow={d.flow} box={BOX.flow} pillar={pillar("breakouts")} judged={d.score?.judged} />
            <SectorCard sectors={d.sectors} box={BOX.sectors} reading={pillar("leadership")?.parts.find((x) => x.id === "sectors")} />
            <SectorRotationCard rotation={d.rotation} />
            {fb && (
              <Inner title={fb.title} box={BOX.feedback}>
                <Charts d={d} id="feedback" />
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
