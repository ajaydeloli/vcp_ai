"use client";

import { useMarket, useMarketHealth, useSummary } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, tone } from "@/lib/fmt";
import { Sparkline } from "./Rings";

const BOX = "flex min-h-[116px] flex-col justify-between rounded-lg border bg-gradient-to-br to-panel p-4";
const CYAN = "#22d3ee";
const ORANGE = "#fb923c";

/** Our own equal-weight index of the scanned universe (not NIFTY: no index data is stored). */
function UniverseIndexTile() {
  const market = useMarket();
  const days = market.data?.days ?? [];
  const last = days[days.length - 1];
  const prev = days[days.length - 2];
  const change =
    last?.index != null && prev?.index != null && prev.index !== 0 ? (last.index / prev.index - 1) * 100 : null;
  return (
    <div
      className={`${BOX} border-accent/40 from-accent/20`}
      title="Equal-weight index of the stocks we scan, 100 on the first day shown. Not NIFTY."
    >
      <p className="text-[11px] font-medium uppercase tracking-wide text-accent">VCP Universe Index</p>
      <div className="flex items-end justify-between gap-3">
        <p className="text-3xl font-semibold leading-tight tabular-nums text-ink">
          {fmtNum(last?.index, 2)}
          <span className={`ml-2 text-xs font-normal ${tone(change)}`}>{fmtPct(change, 2, true)}</span>
        </p>
        <Sparkline values={days.slice(-60).flatMap((d) => (d.index === null ? [] : [d.index]))} color="#4aa3ff" />
      </div>
      <p className="text-xs text-mute">Equal weight, the stocks we scan</p>
    </div>
  );
}

function ScanTile() {
  const s = useSummary().data;
  return (
    <div className={`${BOX} border-[#22d3ee]/40 from-[#22d3ee]/20`}>
      <p className="text-[11px] font-medium uppercase tracking-wide" style={{ color: CYAN }}>
        Today&apos;s scan
      </p>
      <div className="grid grid-cols-3 gap-3">
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
      <p className="text-xs text-mute">{s?.as_of ? `Scan of ${fmtDay(s.as_of)}` : DASH}</p>
    </div>
  );
}

/** NIFTY 50 and SENSEX: no index data is stored yet, so the tile says so rather than show a number. */
function IndexFeedTile({ name, color, box }: { name: string; color: string; box: string }) {
  return (
    <div className={`${BOX} ${box}`} title={`${name} needs an index data feed, not added yet`}>
      <p className="text-[11px] font-medium uppercase tracking-wide" style={{ color }}>
        {name}
      </p>
      <p className="text-3xl font-semibold tabular-nums text-ink" data-testid={`index-${name}`}>
        {DASH}
      </p>
      <p className="text-xs text-mute">Needs an index feed</p>
    </div>
  );
}

const STATUS_COLOR: Record<string, string> = {
  green: "#26c281",
  amber: "#f5a524",
  orange: ORANGE,
  red: "#ef5350",
};
const ARC = Math.PI * 50;

/** The market health score as a half circle, 0 to 100, in the colour of the verdict. */
function ScoreTile() {
  const health = useMarketHealth();
  const v = health.data?.verdict ?? null;
  const color = v ? (STATUS_COLOR[v.status] ?? "#8a97b3") : "#8a97b3";
  const fill = v ? (Math.max(0, Math.min(100, v.score)) / 100) * ARC : 0;
  return (
    <div
      className={`${BOX} items-center`}
      style={{ borderColor: `${color}66`, backgroundImage: `linear-gradient(135deg, ${color}33, transparent 70%)` }}
      title="Average of the Market health readings, each scored 0 to 100. A research read, not a signal."
    >
      <p className="self-start text-[11px] font-medium uppercase tracking-wide" style={{ color }}>
        Market health score
      </p>
      <div className="relative">
        <svg width="150" height="82" viewBox="0 0 120 66" role="img" aria-label={v ? `Market health ${v.score} of 100` : "Market health"}>
          <path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="currentColor" strokeWidth="11" strokeLinecap="round" className="text-line" />
          {v ? (
            <path
              d="M10 60 A50 50 0 0 1 110 60"
              fill="none"
              stroke={color}
              strokeWidth="11"
              strokeLinecap="round"
              strokeDasharray={`${fill} ${ARC}`}
            />
          ) : null}
        </svg>
        <p className="absolute inset-x-0 bottom-0 text-center text-3xl font-semibold leading-none tabular-nums text-ink" data-testid="verdict-score">
          {v ? v.score : DASH}
        </p>
      </div>
      <p className="text-sm font-semibold" style={{ color }} data-testid="verdict-label">
        {v ? v.label : "No reading yet"}
      </p>
    </div>
  );
}

export function Tiles() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <UniverseIndexTile />
      <ScanTile />
      <IndexFeedTile name="NIFTY 50" color="#8b5cf6" box="border-violet/40 from-violet/20" />
      <IndexFeedTile name="SENSEX" color={ORANGE} box="border-[#fb923c]/40 from-[#fb923c]/20" />
      <ScoreTile />
    </div>
  );
}
