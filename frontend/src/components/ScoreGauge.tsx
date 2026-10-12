"use client";

// The Market Health Score as a half-circle gauge: five coloured bands, one per label
// (MARKET_HEALTH_SPECIFICATION §4), a needle at the raw score, the number under it and the label
// after the caps in a pill. The label is written out, so colour never carries it alone.
import { GREY, LABEL_COLOR } from "./MarketParts";

/** Label bands, worst first: [from, to, label] (config/market_health.yaml `labels`). */
export const GAUGE_BANDS: [number, number, string][] = [
  [0, 35, "Adverse"],
  [35, 50, "Weak"],
  [50, 65, "Mixed"],
  [65, 80, "Constructive"],
  [80, 100, "Strong"],
];

const CX = 100;
const CY = 100;
const R = 78;
const WIDTH = 18;
const GAP = 1.2; // score points left empty between two bands

function at(v: number, r: number): [number, number] {
  const a = Math.PI * (1 - v / 100);
  return [CX + r * Math.cos(a), CY - r * Math.sin(a)];
}

function arc(from: number, to: number): string {
  const [x1, y1] = at(from, R);
  const [x2, y2] = at(to, R);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${R} ${R} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

export function ScoreGauge({ raw, label }: { raw: number | null; label: string | null }) {
  const v = raw === null ? null : Math.max(0, Math.min(100, raw));
  const color = label ? (LABEL_COLOR[label] ?? GREY) : GREY;
  const needle = (() => {
    if (v === null) return null;
    const [tx, ty] = at(v, R - 6);
    const a = Math.PI * (1 - v / 100);
    const nx = Math.sin(a) * 4; // half the base width, across the needle
    const ny = Math.cos(a) * 4;
    return `${tx.toFixed(2)},${ty.toFixed(2)} ${(CX - nx).toFixed(2)},${(CY - ny).toFixed(2)} ${(CX + nx).toFixed(2)},${(CY + ny).toFixed(2)}`;
  })();
  return (
    <div className="flex flex-col items-center">
      <svg
        viewBox="0 0 200 112"
        className="h-auto w-full max-w-[13rem]"
        role="img"
        aria-label={v === null ? "Market health score: no reading" : `Market health score ${Math.round(v)} of 100, ${label ?? ""}`}
        data-testid="mhs-gauge"
      >
        {GAUGE_BANDS.map(([from, to, name], i) => (
          <path
            key={name}
            d={arc(from + (i === 0 ? 0 : GAP / 2), to - (i === GAUGE_BANDS.length - 1 ? 0 : GAP / 2))}
            fill="none"
            stroke={LABEL_COLOR[name]}
            strokeWidth={WIDTH}
            strokeLinecap="butt"
          >
            <title>{`${name}: ${from} to ${to === 100 ? 100 : to - 1}`}</title>
          </path>
        ))}
        {needle && <polygon points={needle} fill="#e6ecf8" data-testid="mhs-needle" />}
        <circle cx={CX} cy={CY} r={6} fill="#e6ecf8" />
      </svg>
      <p className="-mt-1 text-4xl font-semibold leading-none tabular-nums text-ink" data-testid="mhs-raw">
        {v === null ? "—" : Math.round(v)}
      </p>
      <p
        className="mt-2 rounded-full px-3 py-0.5 text-xs font-semibold uppercase tracking-wide"
        style={{ color, background: `${color}26` }}
        data-testid="mhs-label"
      >
        {label ?? "No reading"}
      </p>
    </div>
  );
}
