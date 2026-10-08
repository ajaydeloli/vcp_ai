"use client";

import { useMemo, type ReactNode } from "react";
import { useMarket, useMarketHealth, usePaper } from "@/lib/api";
import { fmtDay, fmtPct, strategyLabel } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const DOT: Record<string, string> = {
  green: "bg-up",
  amber: "bg-warn",
  red: "bg-down",
  grey: "bg-mute/60",
};

const VERDICT_TEXT: Record<string, string> = {
  green: "text-up",
  amber: "text-warn",
  orange: "text-[#fb923c]",
  red: "text-down",
};
const VERDICT_BAR: Record<string, string> = {
  green: "bg-up",
  amber: "bg-warn",
  orange: "bg-[#fb923c]",
  red: "bg-down",
};

/** One line on the whole card: the average of the readings' 0-100 scores, with what pulls it up and down. */
function Verdict({ v }: { v: NonNullable<Health["verdict"]> }) {
  return (
    <section aria-label="Verdict" className="mb-4 rounded-lg border border-line bg-panel2 p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-mute">Market health verdict</p>
          <p className={`text-2xl font-semibold ${VERDICT_TEXT[v.status] ?? "text-ink"}`} data-testid="verdict-label">
            {v.label}
          </p>
        </div>
        <p className="text-right">
          <span className="text-4xl font-semibold tabular-nums text-ink" data-testid="verdict-score">
            {v.score}
          </span>
          <span className="text-sm text-mute"> / 100</span>
        </p>
      </div>
      <div className="relative mt-3 h-2 rounded bg-bg" role="img" aria-label={`score ${v.score} of 100`}>
        <span className={`absolute inset-y-0 left-0 rounded ${VERDICT_BAR[v.status] ?? "bg-mute"}`} style={{ width: `${v.score}%` }} />
        {[25, 45, 70].map((t) => (
          <span key={t} className="absolute inset-y-0 w-px bg-ink/40" style={{ left: `${t}%` }} />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-mute">
        <span>Downtrend</span>
        <span>Correction (25)</span>
        <span>Under pressure (45)</span>
        <span>Confirmed uptrend (70)</span>
      </div>
      <p className="mt-3 text-xs text-mute">
        {v.green} green, {v.amber} amber, {v.red} red; {v.counted} readings scored.
        {v.weakest.length > 0 && <> Pulling it down: {v.weakest.join(", ")}.</>}
        {v.strongest.length > 0 && <> Holding it up: {v.strongest.join(", ")}.</>}
        {v.override && <> The index is below its 200-day average, so the verdict is Downtrend whatever the score.</>}
      </p>
    </section>
  );
}

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
    <div className="mb-3 grid grid-cols-3 gap-2 text-xs">
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
      <p className="col-span-3 text-mute">
        The regime is {m.regime_rule}: on when at least {m.breadth_threshold_pct}% of the universe closes above its
        50-day average (the dashed line below).
      </p>
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

function Inner({ g, children }: { g: Health["groups"][number]; children: ReactNode }) {
  return (
    <section aria-label={g.title} className="min-w-0 rounded-lg border border-line bg-panel2 p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-mute">{g.title}</h3>
      <div className="mb-3">{children}</div>
      <ul className="space-y-3 text-xs">
        {g.items.map((i) => (
          <li key={i.id} className="flex gap-2">
            <span
              role="img"
              aria-label={i.status}
              title={i.status === "grey" ? "Not enough data to read" : i.status}
              className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${DOT[i.status] ?? DOT.grey}`}
            />
            <span className="min-w-0">
              <span className="flex justify-between gap-2 font-medium text-ink">
                {i.label}
                {i.score !== null && (
                  <span className="font-normal tabular-nums text-mute" title="score out of 100">
                    {Math.round(i.score)}
                  </span>
                )}
              </span>
              <span className="block text-mute">{i.text}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
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
        <MiniLineChart series={index} height={150} plain label="index-price-chart" />
        <Legend items={[["VCP Universe Index", C.ink], ["50-day", C.warn], ["200-day", C.down]]} />
      </>
    );
  if (id === "leadership")
    return (
      <>
        <MiniLineChart series={highs} height={150} plain label="highs-lows-chart" />
        <Legend items={[["New 52-week highs", C.up], ["New 52-week lows", C.down]]} />
      </>
    );
  if (id === "breadth")
    return (
      <>
        <Regime />
        <MiniLineChart series={breadth} height={110} plain threshold={{ value: 40, title: "40%" }} label="breadth-chart" />
        <Legend items={[["% above 50-day", C.blue], ["% above 200-day", C.violet], ["dashed: regime on at 40%", C.warn]]} />
        <MiniLineChart series={ad} height={90} plain label="ad-line-chart" />
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

/** The market read the way Minervini reads it: price action, leadership, breadth, our own trades. */
export function MarketHealth() {
  const health = useMarketHealth();
  const d = health.data;
  return (
    <Card
      title="Market health"
      subtitle={
        d?.as_of
          ? `Price action, leadership, breadth and our own paper trades · ${fmtDay(d.as_of)} · from our scanned stocks, not NIFTY`
          : "Price action, leadership, breadth and our own paper trades"
      }
      className="min-w-0"
    >
      {health.isError ? (
        <ErrorBox error={health.error} />
      ) : !d ? (
        <Loading what="market health" />
      ) : d.groups.length === 0 ? (
        <Empty>No market data to read yet.</Empty>
      ) : (
        <>
          {d.verdict && <Verdict v={d.verdict} />}
          <div className="grid gap-4 lg:grid-cols-2">
            {d.groups.map((g) => (
              <Inner key={g.id} g={g}>
                <Charts d={d} id={g.id} />
              </Inner>
            ))}
          </div>
          <p className="mt-4 text-[11px] text-mute">
            The verdict is the average of each reading&apos;s 0 to 100 score (grey readings left out). A read for research. It changes no rule, scan or score, and does not feed the regime.
            Colours are display conventions: green healthy, amber mixed, red weak, grey not enough data.
          </p>
        </>
      )}
    </Card>
  );
}
