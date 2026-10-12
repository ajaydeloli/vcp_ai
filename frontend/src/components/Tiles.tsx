"use client";

import { useLiveIndices, useMarket, useMarketHealth, useSummary } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, tone } from "@/lib/fmt";
import { feedNotice, liveLabel, unavailable } from "@/lib/live";
import { EXPOSURE, GREY, LABEL_COLOR } from "./MarketParts";
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
      <p className="text-[11px] font-medium uppercase tracking-wide text-accent">VCP Quality Index (VQI)</p>
      <div className="flex items-end justify-between gap-3">
        <p className="text-3xl font-semibold leading-tight tabular-nums text-ink">
          {fmtNum(last?.index, 2)}
          <span className={`ml-2 text-xs font-normal ${tone(change)}`}>{fmtPct(change, 2, true)}</span>
        </p>
        <Sparkline values={days.slice(-60).flatMap((d) => (d.index === null ? [] : [d.index]))} color="#4aa3ff" />
      </div>
      <p className="text-xs text-mute">
        {last ? `Close of ${fmtDay(last.day)} · ` : ""}equal weight, the stocks we scan, not NIFTY
      </p>
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

/** NIFTY 50 and SENSEX from the live feed (display only; nothing stored, nothing in any rule).
 *  Labelled "Live, delayed N s" or "Close of <date>"; with no price it says why, never 0. */
function IndexLiveTile({ id, name, color, box }: { id: string; name: string; color: string; box: string }) {
  const live = useLiveIndices();
  const idx = live.data?.indices.find((i) => i.id === id);
  const price = idx?.available ? idx.last_price : null;
  const notice = feedNotice(live.data?.feed);
  const note =
    price !== null && idx
      ? liveLabel(idx)
      : live.isError
        ? "Live prices are not reachable"
        : !idx
          ? "Loading live prices"
          : (notice ?? unavailable(idx));
  const line = (idx?.points ?? []).map((p) => p.v);
  return (
    <div
      className={`${BOX} ${box}`}
      title={`${name} from the live feed${idx?.source ? ` (${idx.source})` : ""}. Display only: it feeds no scan, score or rule.`}
    >
      <p className="text-[11px] font-medium uppercase tracking-wide" style={{ color }}>
        {name}
      </p>
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-3xl font-semibold leading-tight tabular-nums text-ink" data-testid={`index-${name}`}>
            {fmtPrice(price)}
          </p>
          {price !== null && idx ? (
            <p className={`text-xs tabular-nums ${tone(idx.change)}`} data-testid={`index-change-${name}`}>
              {idx.change === null ? DASH : `${idx.change > 0 ? "+" : ""}${fmtNum(idx.change, 2)}`} ({fmtPct(idx.change_pct, 2, true)})
            </p>
          ) : null}
        </div>
        {price !== null ? <Sparkline values={line} color={color} /> : null}
      </div>
      <p className="text-xs text-mute" data-testid={`index-note-${name}`}>
        {note}
      </p>
    </div>
  );
}

const ARC = Math.PI * 50;

/** The Market Health Score as a half circle, 0 to 100 (the raw score), in the colour of the label
 *  after the caps. Display and research only. */
function ScoreTile() {
  const health = useMarketHealth();
  const v = health.data?.score ?? null;
  const color = v?.label ? (LABEL_COLOR[v.label] ?? GREY) : GREY;
  const raw = v?.raw ?? null;
  const fill = raw !== null ? (Math.max(0, Math.min(100, raw)) / 100) * ARC : 0;
  const weak = v ? v.pillars.filter((p) => p.score !== null && p.score < 50).map((p) => p.title) : [];
  const light = v?.exposure ? EXPOSURE[v.exposure] : undefined;
  return (
    <div
      className={`${BOX} items-center`}
      style={{ borderColor: `${color}66`, backgroundImage: `linear-gradient(135deg, ${color}33, transparent 70%)` }}
      title={
        "Market Health Score: five pillars weighted 25/25/20/15/15; the label is capped when a warning holds. A research read, not a signal." +
        (v && v.raw_label !== v.label ? ` Raw label ${v.raw_label}.` : "") +
        (weak.length > 0 ? ` Weak: ${weak.join(", ")}.` : "") +
        (v && v.caps.length > 0 ? ` ${v.caps.map((c) => c.reason).join(" ")}` : "") +
        (light ? ` Shadow light: ${light.text}.` : "")
      }
    >
      <p className="self-start text-[11px] font-medium uppercase tracking-wide" style={{ color }}>
        Market health score
      </p>
      <div className="relative">
        <svg width="150" height="82" viewBox="0 0 120 66" role="img" aria-label={raw !== null ? `Market health ${Math.round(raw)} of 100` : "Market health"}>
          <path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="currentColor" strokeWidth="11" strokeLinecap="round" className="text-line" />
          {raw !== null ? (
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
          {raw !== null ? Math.round(raw) : DASH}
        </p>
      </div>
      <p className="text-sm font-semibold" style={{ color }} data-testid="verdict-label">
        {v?.label ?? "No reading yet"}
      </p>
    </div>
  );
}

export function Tiles() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <UniverseIndexTile />
      <ScanTile />
      <IndexLiveTile id="NIFTY50" name="NIFTY 50" color="#8b5cf6" box="border-violet/40 from-violet/20" />
      <IndexLiveTile id="SENSEX" name="SENSEX" color={ORANGE} box="border-[#fb923c]/40 from-[#fb923c]/20" />
      <ScoreTile />
    </div>
  );
}
