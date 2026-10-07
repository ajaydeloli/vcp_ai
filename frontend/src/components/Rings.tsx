// Small SVG charts for the dashboard: a donut (shares of a whole), a score ring (one arc per
// score component, filled in proportion to its score), and a sparkline.

const R = 52;
const C = 2 * Math.PI * R;

export type Segment = { label: string; value: number; color: string };

/** Donut of shares: the segments are scaled to fill the circle. */
export function Donut({
  segments,
  size = 96,
  label,
}: {
  segments: Segment[];
  size?: number;
  label: string;
}) {
  const total = segments.reduce((a, s) => a + Math.max(0, s.value), 0);
  let used = 0;
  return (
    <svg width={size} height={size} viewBox="0 0 120 120" role="img" aria-label={label}>
      <circle cx="60" cy="60" r={R} fill="none" stroke="currentColor" strokeWidth="14" className="text-line" />
      {total > 0
        ? segments.map((s) => {
            const len = (Math.max(0, s.value) / total) * C;
            const el = (
              <circle
                key={s.label}
                cx="60"
                cy="60"
                r={R}
                fill="none"
                stroke={s.color}
                strokeWidth="14"
                strokeDasharray={`${len} ${C - len}`}
                strokeDashoffset={-used}
                transform="rotate(-90 60 60)"
              />
            );
            used += len;
            return el;
          })
        : null}
    </svg>
  );
}

export type Arc = { label: string; value: number | null; max: number; color: string };

/** One arc per component (equal share of the ring), filled by value / max; the centre is the total. */
export function ScoreRing({
  arcs,
  center,
  caption,
  size = 150,
}: {
  arcs: Arc[];
  center: string;
  caption: string;
  size?: number;
}) {
  const n = Math.max(arcs.length, 1);
  const gap = n > 1 ? 6 : 0;
  const span = C / n - gap;
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 120 120" role="img" aria-label={`${caption} ${center}`}>
        {arcs.map((a, i) => {
          const frac = a.value === null || a.max <= 0 ? 0 : Math.min(1, Math.max(0, a.value / a.max));
          const rot = -90 + (i * 360) / n + (gap / C) * 180;
          return (
            <g key={a.label} transform={`rotate(${rot} 60 60)`}>
              <circle
                cx="60"
                cy="60"
                r={R}
                fill="none"
                stroke="currentColor"
                strokeWidth="12"
                strokeDasharray={`${span} ${C - span}`}
                className="text-line"
              />
              {frac > 0 ? (
                <circle
                  cx="60"
                  cy="60"
                  r={R}
                  fill="none"
                  stroke={a.color}
                  strokeWidth="12"
                  strokeDasharray={`${span * frac} ${C - span * frac}`}
                />
              ) : null}
            </g>
          );
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-semibold tabular-nums text-ink">{center}</span>
        <span className="text-[11px] text-mute">{caption}</span>
      </div>
    </div>
  );
}

export function Sparkline({
  values,
  color,
  width = 96,
  height = 32,
}: {
  values: number[];
  color: string;
  width?: number;
  height?: number;
}) {
  if (values.length < 2) return <svg width={width} height={height} aria-hidden="true" />;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const pts = values
    .map((v, i) => `${((i / (values.length - 1)) * width).toFixed(1)},${(height - 2 - ((v - lo) / span) * (height - 4)).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
