"use client";

import { useMemo, type ReactNode } from "react";
import { useMarket, useMarketHealth, usePaper } from "@/lib/api";
import { fmtDay, fmtPct, strategyLabel } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { MarketStage } from "./MarketStage";
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

/** The regime (the breadth rule the scanner already uses), shown with the breadth it is made from. */
function Regime() {
  const market = useMarket(250);
  const m = market.data;
  const last = m?.days[m.days.length - 1];
  if (!m || !last) return null;
  return (
    <div
      className="mb-3 grid grid-cols-3 gap-2 text-xs"
      title={`The regime is ${m.regime_rule}: on when at least ${m.breadth_threshold_pct}% of the universe closes above its 50-day average (the dashed line below).`}
    >
      <div>
        <p className="text-mute">Regime</p>
        <p className={`text-base font-semibold ${last.regime_on ? "text-up" : "text-down"}`}>
          {last.regime_on ? "ON" : "OFF"}
        </p>
      </div>
      <div>
        <p className="text-mute">Breadth</p>
        <p className="text-base font-semibold">{fmtPct(last.breadth_pct)}</p>
      </div>
      <div>
        <p className="text-mute">On, last 20</p>
        <p className="text-base font-semibold">{m.regime_days_on_last_20} / 20</p>
      </div>
    </div>
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

function Inner({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section aria-label={title} className={`min-w-0 rounded-lg border border-line bg-panel2 p-4 ${className}`}>
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

function Charts({ d, id }: { d: Health; id: string }) {
  const pts = d.points;
  const index = useMemo(
    () => [series("Index", C.ink, pts, (p) => p.index), series("50-day", C.warn, pts, (p) => p.ma50), series("200-day", C.down, pts, (p) => p.ma200)],
    [pts],
  );
  const highs = useMemo(() => [series("New highs", C.up, pts, (p) => p.highs), series("New lows", C.down, pts, (p) => p.lows)], [pts]);
  const breadth = useMemo(() => [series("Above 50-day %", C.blue, pts, (p) => p.above50), series("Above 200-day %", C.violet, pts, (p) => p.above200)], [pts]);
  const ad = useMemo(() => [series("A/D line", C.ink, pts, (p) => p.ad_line), series("50-day avg", C.warn, pts, (p) => p.ad_ma50)], [pts]);
  if (id === "index")
    return (
      <>
        <MiniLineChart series={index} height={170} plain label="index-price-chart" />
        <Legend items={[["VCP Universe Index", C.ink], ["50-day", C.warn], ["200-day", C.down]]} />
      </>
    );
  if (id === "leadership")
    return (
      <>
        <MiniLineChart series={highs} height={170} plain label="highs-lows-chart" />
        <Legend items={[["New 52-week highs", C.up], ["New 52-week lows", C.down]]} />
      </>
    );
  if (id === "breadth")
    return (
      <>
        <Regime />
        <MiniLineChart series={breadth} height={100} plain threshold={{ value: 40, title: "40%" }} label="breadth-chart" />
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
  const v = d?.verdict;
  const by = (id: string) => d?.groups.find((g) => g.id === id);
  const idx = by("index");
  const lead = by("leadership");
  const br = by("breadth");
  const fb = by("feedback");
  return (
    <section className="min-w-0" aria-label="Market health overview">
      <header className="mb-3">
        <h2 className="text-sm font-semibold text-ink">Market health</h2>
        <p className="mt-0.5 text-xs text-mute">
          {d?.as_of ? `${fmtDay(d.as_of)} · ` : ""}from our scanned stocks, not NIFTY
          {v ? ` · score ${v.score}, ${v.label}` : ""}
          {v && v.weakest.length > 0 ? ` · pulling it down: ${v.weakest.join(", ")}` : ""}
          {v && v.strongest.length > 0 ? ` · holding it up: ${v.strongest.join(", ")}` : ""}
          {v?.override ? " · index below its 200-day average" : ""}
        </p>
      </header>
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
              <Inner title={idx.title}>
                <Charts d={d} id="index" />
                <Readings group={idx} />
              </Inner>
            )}
            {lead && (
              <Inner title={lead.title}>
                <Charts d={d} id="leadership" />
                <Readings group={lead} />
              </Inner>
            )}
            {br && (
              <Inner title={br.title}>
                <Charts d={d} id="breadth" />
                <Readings group={br} />
              </Inner>
            )}
            {fb && (
              <Inner title={fb.title}>
                <Charts d={d} id="feedback" />
                <Readings group={fb} />
              </Inner>
            )}
            <MarketStage />
            <SetupCounts />
          </div>
          <p className="mt-4 text-[11px] text-mute">
            The score is the average of each reading&apos;s 0 to 100 score (grey readings left out). A read for
            research. It changes no rule, scan or score, and does not feed the regime. Hover a reading for its
            sentence.
          </p>
        </>
      )}
    </section>
  );
}
