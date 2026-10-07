"use client";

import { useMemo, type ReactNode } from "react";
import { useMarketHealth } from "@/lib/api";
import { fmtDay } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { Card, Empty, ErrorBox, Loading } from "./ui";

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
              <span className="block font-medium text-ink">{i.label}</span>
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
    () => [series("Index", C.ink, pts, (p) => p.index), series("50-day", C.warn, pts, (p) => p.ma50), series("150-day", C.violet, pts, (p) => p.ma150), series("200-day", C.down, pts, (p) => p.ma200)],
    [pts],
  );
  const highs = useMemo(() => [series("New highs", C.up, pts, (p) => p.highs), series("New lows", C.down, pts, (p) => p.lows)], [pts]);
  const breadth = useMemo(() => [series("Above 50-day %", C.blue, pts, (p) => p.above50), series("Above 200-day %", C.violet, pts, (p) => p.above200)], [pts]);
  const ad = useMemo(() => [series("A/D line", C.ink, pts, (p) => p.ad_line), series("50-day avg", C.warn, pts, (p) => p.ad_ma50)], [pts]);
  if (id === "index")
    return (
      <>
        <MiniLineChart series={index} height={150} label="index-price-chart" />
        <Legend items={[["VCP Universe Index", C.ink], ["50-day", C.warn], ["150-day", C.violet], ["200-day", C.down]]} />
      </>
    );
  if (id === "leadership")
    return (
      <>
        <MiniLineChart series={highs} height={150} label="highs-lows-chart" />
        <Legend items={[["New 52-week highs", C.up], ["New 52-week lows", C.down]]} />
      </>
    );
  if (id === "breadth")
    return (
      <>
        <MiniLineChart series={breadth} height={110} label="breadth-chart" />
        <Legend items={[["% above 50-day", C.blue], ["% above 200-day", C.violet]]} />
        <MiniLineChart series={ad} height={90} label="ad-line-chart" />
        <Legend items={[["Advance/decline line", C.ink], ["its 50-day average", C.warn]]} />
      </>
    );
  return <Trades trades={d.trades} />;
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
          <div className="grid gap-4 lg:grid-cols-2">
            {d.groups.map((g) => (
              <Inner key={g.id} g={g}>
                <Charts d={d} id={g.id} />
              </Inner>
            ))}
          </div>
          <p className="mt-4 text-[11px] text-mute">
            A read for research. It changes no rule, scan or score, and does not feed the regime.
            Colours are display conventions: green healthy, amber mixed, red weak, grey not enough data.
          </p>
        </>
      )}
    </Card>
  );
}
