"use client";

import { useMemo, type ReactNode } from "react";
import { useMarket, useMarketHealth, usePaper } from "@/lib/api";
import { fmtDay, fmtInt, fmtPct, strategyLabel } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { SetupCounts } from "./SetupCounts";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { Empty, ErrorBox, Loading } from "./ui";

const DOT: Record<string, string> = {
  green: "bg-up",
  amber: "bg-warn",
  red: "bg-down",
  grey: "bg-mute/60",
};

const C = { ink: "#e6e9ef", up: "#26a69a", down: "#ef5350", warn: "#f5a524", violet: "#a78bfa", blue: "#5b9dff" };

function series(name: string, color: string, pts: Health["points"], pick: (p: Health["points"][number]) => number | null): MiniSeries {
  const data: { time: string; value: number }[] = [];
  for (const p of pts) {
    const v = pick(p);
    if (v !== null) data.push({ time: p.day, value: v });
  }
  return { name, color, data };
}

function Legend({ items }: { items: [string, string][] }) {
  return (
    <p className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-mute">
      {items.map(([name, color]) => (
        <span key={name} className="inline-flex items-center gap-1">
          <span className="inline-block h-0.5 w-3" style={{ background: color }} />
          {name}
        </span>
      ))}
    </p>
  );
}

/** Where each strategy stands on the way to its review, and how the open paper positions are doing. */
function PaperProgress() {
  const paper = usePaper();
  const p = paper.data;
  if (!p) return null;
  const open = p.strategies.flatMap((s) => s.open.map((o) => ({ ...o, strategy: s.strategy_id })));
  const max = Math.max(...open.map((o) => Math.abs(o.open_pct ?? 0)), 1);
  return (
    <div className="mt-4 space-y-4 text-xs">
      <div data-testid="mini-paper-progress">
        <p className="mb-2 text-mute">
          Closed trades toward the review (needs {p.min_closed_trades} each, from {fmtDay(p.review_from)})
        </p>
        <ul className="space-y-1.5">
          {p.strategies.map((s) => (
            <li key={s.strategy_id} className="grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-2">
              <span className="text-ink">{strategyLabel(s.strategy_id)}</span>
              <span className="h-2 overflow-hidden rounded bg-bg">
                <span
                  className="block h-full bg-accent"
                  style={{ width: `${Math.min(100, (s.closed / p.min_closed_trades) * 100)}%` }}
                />
              </span>
              <span className="text-right tabular-nums text-mute">
                {s.closed} / {p.min_closed_trades}
              </span>
            </li>
          ))}
        </ul>
      </div>
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
                    <span
                      className={`absolute inset-y-0 ${v >= 0 ? "left-1/2 bg-up" : "right-1/2 bg-down"}`}
                      style={{ width: `${(Math.abs(v) / max) * 50}%` }}
                    />
                  </span>
                  <span className={`text-right tabular-nums ${v >= 0 ? "text-up" : "text-down"}`}>
                    {o.open_pct === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`}
                  </span>
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
            <div className="flex h-1/2 items-end">
              {t.ret_pct > 0 && <div className="w-full bg-up" style={{ height: `${(t.ret_pct / max) * 100}%` }} />}
            </div>
            <div className="flex h-1/2 items-start">
              {t.ret_pct <= 0 && <div className="w-full bg-down" style={{ height: `${(-t.ret_pct / max) * 100}%` }} />}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-1 text-[11px] text-mute">Return of each of the last {trades.length} closed paper trades, oldest first.</p>
    </div>
  );
}

/** All eight dashboard cards are the same height; what does not fit scrolls inside the card. */
const SIZE = "lg:h-[25rem] lg:overflow-y-auto";

/** Each card has its own border colour, with a faint tint of it in the background. */
export const BOX = {
  index: `border-2 border-[#4aa3ff]/60 bg-gradient-to-br from-[#4aa3ff]/10 to-panel2 ${SIZE}`,
  leadership: `border-2 border-[#26c281]/60 bg-gradient-to-br from-[#26c281]/10 to-panel2 ${SIZE}`,
  breadth: `border-2 border-[#8b5cf6]/60 bg-gradient-to-br from-[#8b5cf6]/10 to-panel2 ${SIZE}`,
  feedback: `border-2 border-[#f5a524]/60 bg-gradient-to-br from-[#f5a524]/10 to-panel2 ${SIZE}`,
  stage: `border-2 border-[#22d3ee]/60 bg-gradient-to-br from-[#22d3ee]/10 to-panel2 ${SIZE}`,
  setups: `border-2 border-[#f472b6]/60 bg-gradient-to-br from-[#f472b6]/10 to-panel2 ${SIZE}`,
  paper: `border-2 border-[#fb923c]/60 bg-gradient-to-br from-[#fb923c]/10 to-panel2 ${SIZE}`,
  activity: `border-2 border-[#a3e635]/60 bg-gradient-to-br from-[#a3e635]/10 to-panel2 ${SIZE}`,
};

function Inner({ title, box, children }: { title: string; box: string; children: ReactNode }) {
  return (
    <section aria-label={title} className={`min-w-0 rounded-lg p-4 ${box}`}>
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-mute">{title}</h3>
      {children}
    </section>
  );
}

/** The readings as one line each: dot, name, the figure, its 0 to 100 score. The sentence is the tooltip. */
function Readings({ group }: { group: Health["groups"][number] }) {
  return (
    <>
      <ul className="mt-3 divide-y divide-line/60 text-xs">
        {group.items.map((i) => (
          <li key={i.id} className="flex items-center gap-2 py-1.5" title={i.text}>
            <span
              role="img"
              aria-label={i.status}
              className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[i.status] ?? DOT.grey}`}
            />
            <span className="min-w-0 flex-1 truncate text-ink">{i.label}</span>
            <span className="shrink-0 text-right text-mute">{i.short}</span>
            <span className="w-7 shrink-0 text-right tabular-nums text-ink" title="score out of 100">
              {i.score === null ? "" : Math.round(i.score)}
            </span>
          </li>
        ))}
      </ul>
      <details className="mt-2 text-[11px] text-mute">
        <summary className="cursor-pointer hover:text-ink">Details</summary>
        <ul className="mt-1 space-y-1.5">
          {group.items.map((i) => (
            <li key={i.id}>{i.text}</li>
          ))}
        </ul>
      </details>
    </>
  );
}

function Figure({ label, value, sub, color }: { label: string; value: string; sub?: string; color: string }) {
  return (
    <div>
      <p className="text-sm text-ink">{label}</p>
      <p className="flex flex-wrap items-baseline gap-x-1.5">
        <span className={`text-2xl font-semibold tabular-nums ${color}`}>{value}</span>
        {sub ? <span className="text-xs text-mute">{sub}</span> : null}
      </p>
    </div>
  );
}

/** The three figures beside the index chart: distance from the 200-day, accumulation days, distribution days. */
function IndexFigures({ days }: { days: NonNullable<Health["index_days"]> }) {
  const from = days.pct_from_200;
  const last = `(last ${days.window} sessions)`;
  return (
    <aside aria-label="Index figures" className="w-40 shrink-0 space-y-3 border-l border-line pl-4">
      <Figure
        label="Index vs 200-day average"
        value={from === null ? "—" : `${from >= 0 ? "+" : ""}${from.toFixed(1)}%`}
        color={from === null ? "text-ink" : from >= 0 ? "text-up" : "text-down"}
      />
      <div className="border-t border-line pt-3">
        <Figure label="Accumulation days" value={String(days.accumulation)} sub={last} color="text-up" />
      </div>
      <div className="border-t border-line pt-3">
        <Figure label="Distribution days" value={String(days.distribution)} sub={last} color="text-down" />
      </div>
    </aside>
  );
}

/** The figures beside the leadership chart: new highs, new lows, failed breakouts, leaders against the index. */
function LeadershipFigures({ group, highs, lows }: { group: Health["groups"][number]; highs: number | null; lows: number | null }) {
  const num = (id: string) => group.items.find((i) => i.id === id)?.value ?? null;
  const failed = num("failed_breakouts");
  const lead = num("leaders");
  return (
    <aside aria-label="Leadership figures" className="w-40 shrink-0 space-y-3 border-l border-line pl-4">
      <Figure label="New 52-week highs" value={highs === null ? "—" : String(highs)} sub="today" color="text-up" />
      <div className="border-t border-line pt-3">
        <Figure label="New 52-week lows" value={lows === null ? "—" : String(lows)} sub="today" color="text-down" />
      </div>
      <div className="border-t border-line pt-3">
        <Figure
          label="Failed breakouts"
          value={failed === null ? "—" : `${failed.toFixed(0)}%`}
          color={failed === null ? "text-ink" : failed > 50 ? "text-down" : failed > 25 ? "text-warn" : "text-up"}
        />
      </div>
      <div className="border-t border-line pt-3">
        <Figure
          label="Leaders vs index"
          value={lead === null ? "—" : `${lead >= 0 ? "+" : ""}${lead.toFixed(1)}`}
          sub="points"
          color={lead === null ? "text-ink" : lead >= 0 ? "text-up" : "text-down"}
        />
      </div>
    </aside>
  );
}

/** The figures beside the breadth charts: the scanner's regime, the two breadth shares, net advancers. */
function BreadthFigures({ group }: { group: Health["groups"][number] }) {
  const market = useMarket(250);
  const m = market.data;
  const last = m?.days[m.days.length - 1];
  const num = (id: string) => group.items.find((i) => i.id === id)?.value ?? null;
  const pct = (v: number | null) => (v === null ? "—" : `${v.toFixed(1)}%`);
  const above = (v: number | null) => (v === null ? "text-ink" : v >= 50 ? "text-up" : v >= 40 ? "text-warn" : "text-down");
  const a50 = num("above50");
  const a200 = num("above200");
  const net = num("ad");
  return (
    <aside aria-label="Breadth figures" className="w-40 shrink-0 space-y-3 border-l border-line pl-4">
      <div
        title={m ? `The regime is ${m.regime_rule}: on when at least ${m.breadth_threshold_pct}% of the universe closes above its 50-day average (the dashed line).` : undefined}
      >
        <Figure
          label="Regime"
          value={last ? (last.regime_on ? "ON" : "OFF") : "—"}
          sub={m ? `(${m.regime_days_on_last_20}/20 days on)` : undefined}
          color={last ? (last.regime_on ? "text-up" : "text-down") : "text-ink"}
        />
      </div>
      <div className="border-t border-line pt-3">
        <Figure label="Above 50-day average" value={pct(a50)} color={above(a50)} />
      </div>
      <div className="border-t border-line pt-3">
        <Figure label="Above 200-day average" value={pct(a200)} color={above(a200)} />
      </div>
      <div className="border-t border-line pt-3">
        <Figure
          label="Net advancers"
          value={net === null ? "—" : `${net >= 0 ? "+" : ""}${net.toFixed(0)}`}
          sub="today"
          color={net === null ? "text-ink" : net >= 0 ? "text-up" : "text-down"}
        />
      </div>
    </aside>
  );
}

const STAGES = [
  { key: "stage1", label: "Stage 1: base", color: C.blue, text: "text-accent" },
  { key: "stage2", label: "Stage 2: uptrend", color: C.up, text: "text-up" },
  { key: "stage3", label: "Stage 3: top", color: C.warn, text: "text-warn" },
  { key: "stage4", label: "Stage 4: downtrend", color: C.down, text: "text-down" },
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
              <MiniLineChart series={lines} height={200} plain label="stage-chart" />
              <Legend items={STAGES.map((st) => [st.label, st.color])} />
            </div>
            <aside aria-label="Stage figures" className="w-40 shrink-0 space-y-3 border-l border-line pl-4">
              {STAGES.map((st, i) => (
                <div key={st.key} className={i ? "border-t border-line pt-3" : ""}>
                  <Figure
                    label={st.label}
                    value={fmtPct((last[st.key] / last.total) * 100, 0)}
                    sub={`(${fmtInt(last[st.key])} stocks)`}
                    color={st.text}
                  />
                </div>
              ))}
            </aside>
          </div>
          <p className="mt-2 text-[11px] text-mute">
            Share of {fmtInt(last.total)} scanned stocks in each weekly stage, week by week; {fmtInt(last.transition)} are
            between stages (not drawn). The stocks are today&apos;s scan list, the stage is worked out for each past week
            with the scan&apos;s own rule.
          </p>
        </>
      )}
    </Inner>
  );
}

function Charts({ d, id }: { d: Health; id: string }) {
  const pts = d.points;
  const index = useMemo(
    () => [series("VQI", C.ink, pts, (p) => p.index), series("50-day", C.warn, pts, (p) => p.ma50), series("200-day", C.down, pts, (p) => p.ma200)],
    [pts],
  );
  const highs = useMemo(() => [series("New highs", C.up, pts, (p) => p.highs), series("New lows", C.down, pts, (p) => p.lows)], [pts]);
  const breadth = useMemo(() => [series("Above 50-day %", C.blue, pts, (p) => p.above50), series("Above 200-day %", C.violet, pts, (p) => p.above200)], [pts]);
  const ad = useMemo(() => [series("A/D line", C.ink, pts, (p) => p.ad_line), series("50-day avg", C.warn, pts, (p) => p.ad_ma50)], [pts]);
  if (id === "index")
    return (
      <>
        <MiniLineChart series={index} height={200} plain label="index-price-chart" />
        <Legend items={[["VQI", C.ink], ["50-day", C.warn], ["200-day", C.down]]} />
      </>
    );
  if (id === "leadership")
    return (
      <>
        <MiniLineChart series={highs} height={200} plain whole label="highs-lows-chart" />
        <Legend items={[["New 52-week highs", C.up], ["New 52-week lows", C.down]]} />
      </>
    );
  if (id === "breadth")
    return (
      <>
        <MiniLineChart series={breadth} height={110} plain threshold={{ value: 40, title: "40%" }} label="breadth-chart" />
        <Legend items={[["% above 50-day", C.blue], ["% above 200-day", C.violet], ["dashed: regime on at 40%", C.warn]]} />
        <MiniLineChart series={ad} height={80} plain label="ad-line-chart" />
        <Legend items={[["Advance/decline line", C.ink], ["its 50-day average", C.warn]]} />
      </>
    );
  return (
    <>
      <Trades trades={d.trades} />
      <PaperProgress />
    </>
  );
}

/** The market read the way Minervini reads it: price action, leadership, breadth, our own trades, stages, setups. */
export function MarketHealth() {
  const health = useMarketHealth();
  const d = health.data;
  const by = (id: string) => d?.groups.find((g) => g.id === id);
  const idx = by("index");
  const lead = by("leadership");
  const br = by("breadth");
  const fb = by("feedback");
  return (
    <section className="min-w-0" aria-label="Market health overview">
      {health.isError ? (
        <ErrorBox error={health.error} />
      ) : !d ? (
        <Loading what="market health" />
      ) : d.groups.length === 0 ? (
        <Empty>No market data to read yet.</Empty>
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            {idx && (
              <Inner title={idx.title} box={BOX.index}>
                <div className="flex gap-4">
                  <div className="min-w-0 flex-1">
                    <Charts d={d} id="index" />
                  </div>
                  {d.index_days && <IndexFigures days={d.index_days} />}
                </div>
              </Inner>
            )}
            {lead && (
              <Inner title={lead.title} box={BOX.leadership}>
                <div className="flex gap-4">
                  <div className="min-w-0 flex-1">
                    <Charts d={d} id="leadership" />
                  </div>
                  <LeadershipFigures
                    group={lead}
                    highs={d.points.at(-1)?.highs ?? null}
                    lows={d.points.at(-1)?.lows ?? null}
                  />
                </div>
              </Inner>
            )}
            {br && (
              <Inner title={br.title} box={BOX.breadth}>
                <div className="flex gap-4">
                  <div className="min-w-0 flex-1">
                    <Charts d={d} id="breadth" />
                  </div>
                  <BreadthFigures group={br} />
                </div>
              </Inner>
            )}
            <StageCard stages={d.stages} box={BOX.stage} />
            {fb && (
              <Inner title={fb.title} box={BOX.feedback}>
                <Charts d={d} id="feedback" />
                <Readings group={fb} />
              </Inner>
            )}
            <SetupCounts box={BOX.setups} />
          </div>
          <p className="mt-4 text-[11px] text-mute">
            Everything here is read from our scanned stocks, not NIFTY. The score is the average of each reading&apos;s 0 to 100 score (grey readings left out). A read for
            research. It changes no rule, scan or score, and does not feed the regime. Hover a reading for its
            sentence.
          </p>
        </>
      )}
    </section>
  );
}
