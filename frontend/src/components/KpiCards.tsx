"use client";

import { useMarket, usePaper, useSummary } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtPct, strategyLabel } from "@/lib/fmt";

function Kpi({
  title,
  value,
  sub,
  tone = "text-ink",
  hint,
}: {
  title: string;
  value: string;
  sub?: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-panel p-4" title={hint}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-mute">{title}</p>
      <p className={`mt-2 text-3xl font-semibold ${tone}`} data-testid={`kpi-${title}`}>
        {value}
      </p>
      <p className="mt-1 min-h-4 text-xs text-mute">{sub}</p>
    </div>
  );
}

export function KpiCards() {
  const summary = useSummary();
  const market = useMarket();
  const paper = usePaper();

  const s = summary.data;
  const grade2 = s ? s.strategies.reduce((a, x) => a + x.grade2_plus, 0) : null;
  const breakouts = s ? s.strategies.reduce((a, x) => a + x.breakouts, 0) : null;
  const perStrategy = s
    ? s.strategies.map((x) => `${strategyLabel(x.strategy_id)} ${x.grade2_plus}`).join(" · ")
    : undefined;

  const last = market.data?.days[market.data.days.length - 1];
  const regime = last ? (last.regime_on ? "ON" : "OFF") : DASH;

  const closed = paper.data ? paper.data.strategies.reduce((a, x) => a + x.closed, 0) : null;

  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-5">
      <Kpi
        title="Universe scanned"
        value={fmtInt(s?.universe_size)}
        sub={s ? `${fmtInt(s.trend_template_pass)} pass the Trend Template` : undefined}
        hint="Stocks in our NSE universe on the scan date"
      />
      <Kpi
        title="Grade 2+ setups"
        value={fmtInt(grade2)}
        sub={perStrategy}
        hint={perStrategy}
        tone="text-accent"
      />
      <Kpi
        title="Breakouts today"
        value={fmtInt(breakouts)}
        sub={s?.as_of ? `Scan of ${fmtDay(s.as_of)}` : undefined}
        tone="text-warn"
      />
      <Kpi
        title="Market regime"
        value={regime}
        sub={
          last
            ? `Breadth ${fmtPct(last.breadth_pct)} · entries need ≥ ${market.data?.breadth_threshold_pct}%`
            : undefined
        }
        tone={last ? (last.regime_on ? "text-up" : "text-down") : "text-ink"}
        hint="Paper rule breadth50: new paper entries only while enough of our universe is above its 50-day average"
      />
      <Kpi
        title="Paper positions open"
        value={fmtInt(paper.data?.open_positions)}
        sub={
          paper.data
            ? closed === 0 && paper.data.open_positions === 0
              ? "No paper trades yet"
              : `${closed} closed so far`
            : undefined
        }
      />
    </div>
  );
}
