"use client";

// The Market Health Score's own readings, shown beside the cards' charts (FRONTEND_SPECIFICATION
// §67.34): the same values and the same colours as the score, so a card never tells a different
// story from the score above it.
import type { MarketHealth as Health } from "@/lib/schemas";

export type Pillar = NonNullable<Health["score"]>["pillars"][number];
type Part = Pillar["parts"][number];

const YES_NO = new Set(["nifty50", "smallcap250"]);

/** A reading's colour by its points in the score: green 65+, amber 50–64, red below 50. */
export function pointsClass(points: number | null): string {
  return points === null ? "text-mute" : points >= 65 ? "text-up" : points >= 50 ? "text-warn" : "text-down";
}

/** Readings that are a change or a gap, shown with their sign. */
const SIGNED = new Set([
  "index_trend.above_50",
  "index_trend.above_150",
  "index_trend.above_200",
  "index_trend.rising_200",
  "breadth.ad_trend",
  "breadth.change",
  "breadth.vqi_gap",
  "leadership.leader_change",
  "leadership.leaders_vs_vqi",
  "breakouts.median_return",
]);

export function fmtPart(x: Part, pillarId: string): string {
  const v = x.reading;
  if (v === null) return "—";
  if (YES_NO.has(x.id)) return v >= 1 ? "Yes" : "No";
  if (x.id === "stacked") return `${v.toFixed(0)} of 2`;
  const a = Math.abs(v);
  const s = a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
  return v > 0 && SIGNED.has(`${pillarId}.${x.id}`) ? `+${s}` : s;
}

/** One pillar's readings as a compact list: label, value coloured by its points. */
export function ScoreReadings({ pillar, title }: { pillar: Pillar | undefined; title?: string }) {
  if (!pillar) return null;
  return (
    <div role="group" aria-label={`${title ?? pillar.title} readings`} className="space-y-1">
      <h4 className="flex justify-between text-[10px] font-semibold uppercase tracking-wide text-mute">
        <span>{title ?? pillar.title}</span>
        <span className={`tabular-nums ${pointsClass(pillar.score)}`} title="The pillar's score, 0 to 100">
          {pillar.score === null ? "missing" : Math.round(pillar.score)}
        </span>
      </h4>
      <ul className="space-y-0.5">
        {pillar.parts.map((x) => (
          <li
            key={x.id}
            className="flex items-baseline justify-between gap-2 text-[11px]"
            title={x.points === null ? "Not measured" : `${Math.round(x.points)} of 100 points, weight ${x.weight}`}
          >
            <span className="min-w-0 text-mute">{x.label}</span>
            <span className={`shrink-0 font-medium tabular-nums ${pointsClass(x.points)}`} data-testid={`reading-${pillar.id}-${x.id}`}>
              {fmtPart(x, pillar.id)}
            </span>
          </li>
        ))}
      </ul>
      {pillar.note && <p className="text-[11px] text-mute">{pillar.note}</p>}
    </div>
  );
}
