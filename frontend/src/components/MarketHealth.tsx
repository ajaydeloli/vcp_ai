"use client";

import { useMemo } from "react";
import { useMarket, useMarketHealth, usePaper } from "@/lib/api";
import { fmtDay, fmtInt, fmtPct, strategyLabel } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { SetupCounts } from "./SetupCounts";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { SetupFlowCard } from "./MarketFlow";
import { SectorCard } from "./SectorStrength";
import { BOX, C, Figure, Inner, Legend, NSE } from "./MarketParts";
import { Empty, ErrorBox, Loading } from "./ui";

const DOT: Record<string, string> = {
  green: "bg-up",
  amber: "bg-warn",
  red: "bg-down",
  grey: "bg-mute/60",
};


function series(name: string, color: string, pts: Health["points"], pick: (p: Health["points"][number]) => number | null): MiniSeries {
  const data: { time: string; value: number }[] = [];
  for (const p of pts) {
    const v = pick(p);
    if (v !== null) data.push({ time: p.day, value: v });
  }
  return { name, color, data };
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
    </>
  );
}

/** The figures beside the index chart: distance from the 50-day and 200-day averages, accumulation days, distribution days. */
function IndexFigures({ days }: { days: NonNullable<Health["index_days"]> }) {
  const last = `(last ${days.window} sessions)`;
  const away = (label: string, v: number | null, first = false) => (
    <div className={first ? "" : "border-t border-line pt-3"}>
      <Figure
        label={label}
        value={v === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`}
        color={v === null ? "text-ink" : v >= 0 ? "text-up" : "text-down"}
      />
    </div>
  );
  return (
    <aside aria-label="Index figures" className="w-40 shrink-0 space-y-3 border-l border-line pl-4">
      {away("VQI vs 50-day MA", days.pct_from_50, true)}
      {away("VQI vs 200-day MA", days.pct_from_200)}
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
              <MiniLineChart series={lines} height={262} plain label="stage-chart" />
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
        </>
      )}
    </Inner>
  );
}

function Charts({ d, id }: { d: Health; id: string }) {
  const pts = d.points;
  const index = useMemo(
    () => [
      {
        ...series("VQI", C.ink, pts, (p) => p.index),
        // distribution days: VQI down 0.2 % or more on higher volume (the count beside the chart)
        markers: pts.filter((p) => p.distribution).map((p) => ({ time: p.day, color: C.down, position: "aboveBar" as const })),
      },
      series("50-day", C.warn, pts, (p) => p.ma50),
      series("200-day", C.down, pts, (p) => p.ma200),
      series("NIFTY 50", NSE.nifty50, pts, (p) => p.nifty50),
      series("NIFTY 500", NSE.nifty500, pts, (p) => p.nifty500),
    ],
    [pts],
  );
  const hasNse = pts.some((p) => p.nifty50 !== null || p.nifty500 !== null);
  const highs = useMemo(
    () => [
      { ...series("Net new highs", C.blue, pts, (p) => p.net_highs), kind: "bar" as const },
      series("10-day average", C.warn, pts, (p) => p.net_highs_avg10),
    ],
    [pts],
  );
  const breadth = useMemo(() => [series("Above 50-day %", C.blue, pts, (p) => p.above50), series("Above 200-day %", C.violet, pts, (p) => p.above200)], [pts]);
  const ad = useMemo(() => [series("A/D line", C.ink, pts, (p) => p.ad_line), series("50-day avg", C.warn, pts, (p) => p.ad_ma50)], [pts]);
  if (id === "index")
    return (
      <>
        <MiniLineChart series={index} height={262} plain label="index-price-chart" />
        <Legend
          items={[
            ["VQI", C.ink],
            ["its 50-day", C.warn],
            ["its 200-day", C.down],
            ["● distribution day", C.down],
            ...(hasNse ? ([["NIFTY 50", NSE.nifty50], ["NIFTY 500", NSE.nifty500]] as [string, string][]) : []),
          ]}
        />
        <p className="text-[11px] text-mute">
          {hasNse
            ? "All lines start at 100. NIFTY rising while VQI lags: a few large stocks carry the market."
            : "NIFTY lines appear once the index closes are fetched (vcp indices update)."}
        </p>
      </>
    );
  if (id === "leadership")
    return (
      <>
        <MiniLineChart series={highs} height={200} plain label="highs-lows-chart" />
        <Legend items={[["Net new 52-week highs (highs − lows) a day", C.blue], ["its 10-day average", C.warn]]} />
        <NewHighsList highs={d.new_highs} />
      </>
    );
  if (id === "breadth")
    return (
      <>
        <MiniLineChart series={breadth} height={119} plain threshold={{ value: 40, title: "40%" }} label="breadth-chart" />
        <Legend items={[["% above 50-day", C.blue], ["% above 200-day", C.violet], ["dashed: regime on at 40%", C.warn]]} />
        <MiniLineChart series={ad} height={119} plain label="ad-line-chart" />
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
          <span aria-hidden="true" className="text-warn">⚠</span>
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
          <Warnings warnings={d.warnings} />
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
            <SetupFlowCard flow={d.flow} box={BOX.flow} />
            <SetupCounts box={BOX.setups} />
            {fb && (
              <Inner title={fb.title} box={BOX.feedback}>
                <Charts d={d} id="feedback" />
                <Readings group={fb} />
              </Inner>
            )}
            <SectorCard sectors={d.sectors} box={BOX.sectors} />
          </div>
        </>
      )}
    </section>
  );
}
