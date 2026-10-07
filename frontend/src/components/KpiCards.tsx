"use client";

import { useMarket, useSetups, useSummary } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtPct } from "@/lib/fmt";
import { countList } from "@/lib/lists";
import { Donut } from "./Rings";

function Tile({
  title,
  value,
  sub,
  className,
  titleClass,
  hint,
}: {
  title: string;
  value: string;
  sub?: string;
  className: string;
  titleClass: string;
  hint?: string;
}) {
  return (
    <div className={`rounded-lg border p-4 ${className}`} title={hint}>
      <p className={`text-[11px] font-medium uppercase tracking-wide ${titleClass}`}>{title}</p>
      <p className="mt-2 text-4xl font-semibold text-ink" data-testid={`kpi-${title}`}>
        {value}
      </p>
      <p className="mt-1 min-h-4 text-xs text-mute">{sub}</p>
    </div>
  );
}

export function KpiCards() {
  const summary = useSummary();
  const market = useMarket();
  const vcp = useSetups("vcp");

  const rows = vcp.data?.rows ?? null;
  const n = (id: string): string => (rows ? fmtInt(countList(rows, id)) : DASH);
  const s = summary.data;
  const last = market.data?.days[market.data.days.length - 1];
  const above = last?.breadth_pct ?? null;

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 2xl:grid-cols-[repeat(4,minmax(0,1fr))_minmax(0,1.4fr)_minmax(0,1.4fr)]">
      <Tile
        title="A+ VCP setups"
        value={n("aplus")}
        sub="Grade 3 in the VCP list"
        className="border-up/40 bg-gradient-to-br from-up/20 to-panel"
        titleClass="text-up"
      />
      <Tile
        title="VCP setups"
        value={n("vcp")}
        sub={rows ? `${countList(rows, "vcp_like")} more are VCP like` : undefined}
        className="border-accent/40 bg-gradient-to-br from-accent/20 to-panel"
        titleClass="text-accent"
      />
      <Tile
        title="Forming bases"
        value={n("forming")}
        sub="Pivot not reached yet"
        className="border-violet/40 bg-gradient-to-br from-violet/20 to-panel"
        titleClass="text-violet"
      />
      <Tile
        title="Breakout watch"
        value={n("watch")}
        sub="Pivot ready or broken out"
        className="border-warn/40 bg-gradient-to-br from-warn/20 to-panel"
        titleClass="text-warn"
      />

      <div className="rounded-lg border border-line bg-panel p-4">
        <p className="text-[11px] font-medium uppercase tracking-wide text-mute">Today&apos;s scan</p>
        <div className="mt-3 grid grid-cols-3 gap-3">
          {[
            ["symbols", s?.universe_size],
            ["scanned", s?.scanned],
            ["Trend Template", s?.trend_template_pass],
          ].map(([label, v]) => (
            <div key={String(label)}>
              <p className="text-xl font-semibold tabular-nums text-ink">{fmtInt(v as number | null | undefined)}</p>
              <p className="text-[11px] leading-tight text-mute">{label}</p>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-mute">{s?.as_of ? `Scan of ${fmtDay(s.as_of)}` : ""}</p>
      </div>

      <div
        className="rounded-lg border border-line bg-panel p-4"
        title="Paper rule breadth50: new paper entries only while enough of our universe is above its 50-day average"
      >
        <p className="text-[11px] font-medium uppercase tracking-wide text-mute">Market breadth</p>
        <div className="mt-2 flex items-center gap-3">
          <Donut
            label="Share of our universe above and below its 50-day average"
            segments={[
              { label: "above", value: above ?? 0, color: "#26c281" },
              { label: "below", value: above === null ? 0 : 100 - above, color: "#ef5350" },
            ]}
          />
          <dl className="space-y-1 text-xs">
            <div className="flex justify-between gap-3">
              <dt className="text-mute">Above 50-day</dt>
              <dd className="tabular-nums text-up" data-testid="kpi-breadth-above">{fmtPct(above)}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-mute">Below</dt>
              <dd className="tabular-nums text-down">{fmtPct(above === null ? null : 100 - above)}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-mute">Regime</dt>
              <dd className={last ? (last.regime_on ? "text-up" : "text-down") : "text-ink"} data-testid="kpi-regime">
                {last ? (last.regime_on ? "ON" : "OFF") : DASH}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  );
}
