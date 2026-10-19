"use client";

// Sector rotation (FRONTEND_SPECIFICATION §67.33): NSE's sector indices against NIFTY 500 on a
// relative rotation graph, one point a week. RS-Ratio across (above 100: stronger than its own
// 10-week trend against NIFTY 500), RS-Momentum up (above 100: that strength is rising). Sectors
// usually turn clockwise: Improving → Leading → Weakening → Lagging. Display only.
import { useMemo, useState } from "react";
import { fmtDay } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { BOX, Inner } from "./MarketParts";
import { Empty } from "./ui";

type Rotation = Health["rotation"][number];
type Quadrant = Rotation["quadrant"];

/** Quadrant colours: the dashboard's state colours; the quadrant is also named in each corner,
 *  in the tooltip and in the list, so colour never carries it alone. */
export const QUADRANT: Record<Quadrant, { name: string; color: string }> = {
  leading: { name: "Leading", color: "#26c281" },
  weakening: { name: "Weakening", color: "#f5a524" },
  lagging: { name: "Lagging", color: "#ef5350" },
  improving: { name: "Improving", color: "#5b9dff" },
};
const LIST_ORDER: Quadrant[] = ["leading", "improving", "weakening", "lagging"];

/** Short names for the chart labels; the full name is in the tooltip and the list. */
const SHORT: Record<string, string> = {
  "Automobile and Auto Components": "Auto",
  "Capital Goods": "Cap Goods",
  Chemicals: "Chemicals",
  Construction: "Constr.",
  "Construction Materials": "Cement",
  "Consumer Durables": "Cons Dur.",
  "Consumer Services": "Cons Svcs",
  "Fast Moving Consumer Goods": "FMCG",
  "Financial Services": "Financials",
  Healthcare: "Healthcare",
  "Information Technology": "IT",
  "Media Entertainment & Publication": "Media",
  "Metals & Mining": "Metals",
  "Oil Gas & Consumable Fuels": "Oil & Gas",
  Power: "Power",
  Realty: "Realty",
  Services: "Services",
  Telecommunication: "Telecom",
};
const short = (s: string) => SHORT[s] ?? s;

const TAILS = [4, 6, 12] as const;
const SIZE = 400;
const PAD = 30;
/** Rough size of the hover tooltip in chart units, to pick a corner clear of the track. */
const TIP_W = 140;
const TIP_H = 44;
const SURFACE = "#111a2c"; // panel2: the ring around each head

function domain(values: number[]): number {
  // symmetric around 100, a little room outside the furthest point, at least ±2
  const far = Math.max(2, ...values.map((v) => Math.abs(v - 100)));
  return Math.ceil(far * 1.15 * 2) / 2;
}

function ticks(half: number): number[] {
  const step = half > 8 ? 4 : half > 4 ? 2 : 1;
  const out: number[] = [];
  for (let v = -Math.floor(half / step) * step; v <= half; v += step) out.push(100 + v);
  return out;
}

export function SectorRotationCard({ rotation }: { rotation: Health["rotation"] }) {
  const [tail, setTail] = useState<(typeof TAILS)[number]>(6);
  const [focus, setFocus] = useState<string | null>(null);
  const shown = useMemo(() => rotation.map((r) => ({ ...r, points: r.points.slice(-tail) })), [rotation, tail]);
  const hx = domain(shown.flatMap((r) => r.points.map((p) => p.ratio)));
  const hy = domain(shown.flatMap((r) => r.points.map((p) => p.momentum)));
  const inner = SIZE - 2 * PAD;
  const x = (v: number) => PAD + ((v - (100 - hx)) / (2 * hx)) * inner;
  const y = (v: number) => PAD + (1 - (v - (100 - hy)) / (2 * hy)) * inner;
  // label positions: beside each head, moved down a line when they would overlap another label
  const labels = useMemo(() => {
    const placed: { sector: string; lx: number; ly: number; right: boolean; w: number }[] = [];
    const heads = shown
      .flatMap((r) => {
        const p = r.points.at(-1);
        return p ? [{ r, p }] : [];
      })
      .sort((a, b) => y(a.p.momentum) - y(b.p.momentum));
    for (const { r, p } of heads) {
      const cx = x(p.ratio);
      const right = cx < SIZE - PAD - 60;
      const w = short(r.sector).length * 5.6;
      const lx = right ? cx + 7 : cx - 7;
      const left = right ? lx : lx - w;
      const base = y(p.momentum) + 3;
      let ly = base;
      const clash = () =>
        placed.some((o) => {
          const oLeft = o.right ? o.lx : o.lx - o.w;
          return Math.abs(o.ly - ly) < 10.5 && left < oLeft + o.w && oLeft < left + w;
        });
      // the nearest free line: just below, just above, two below, …
      for (const k of [1, -1, 2, -2, 3, -3, 4, -4]) {
        if (!clash()) break;
        ly = base + k * 10.5;
      }
      placed.push({ sector: r.sector, lx, ly, right, w });
    }
    return new Map(placed.map((l) => [l.sector, l]));
    // x and y depend only on the domains, which depend on `shown`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown]);
  const active = shown.find((r) => r.sector === focus) ?? null;
  const head = active?.points.at(-1);
  // the tooltip sits in the chart corner that is clearest of the hovered track,
  // so it never hides the tail it describes
  const tipCorner = useMemo(() => {
    if (!active) return null;
    const pts: [number, number][] = [];
    active.points.forEach((p, i) => {
      pts.push([x(p.ratio), y(p.momentum)]);
      const q = active.points[i + 1];
      if (q) pts.push([(x(p.ratio) + x(q.ratio)) / 2, (y(p.momentum) + y(q.momentum)) / 2]);
    });
    const corners = (["tl", "tr", "bl", "br"] as const).map((c) => {
      const left = c.endsWith("l") ? PAD + 4 : SIZE - PAD - 4 - TIP_W;
      const top = c.startsWith("t") ? PAD + 20 : SIZE - PAD - 20 - TIP_H;
      const cx = left + TIP_W / 2;
      const cy = top + TIP_H / 2;
      const hits = pts.filter(([px, py]) => px > left - 8 && px < left + TIP_W + 8 && py > top - 8 && py < top + TIP_H + 8).length;
      const near = Math.min(...pts.map(([px, py]) => Math.hypot(px - cx, py - cy)));
      return { c, hits, near };
    });
    corners.sort((a, b) => a.hits - b.hits || b.near - a.near);
    return corners[0]?.c ?? "tr";
    // x and y depend only on the domains, which depend on `shown`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, shown]);
  const week = shown[0]?.points.at(-1)?.day;
  // sectors with fewer weeks than the others (indices NSE started recently)
  const longest = Math.min(tail, Math.max(0, ...rotation.map((r) => r.points.length)));
  const short_ = shown.filter((r) => r.points.length < longest);

  return (
    <Inner title="Sector rotation" box={BOX.rotation} fill>
      {shown.length === 0 ? (
        <Empty>Not enough weekly history of the sector indices yet.</Empty>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3 lg:grid lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)] lg:gap-4">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="mb-1 flex shrink-0 items-center justify-between text-[11px] text-mute">
              <span>Sector indices vs NIFTY 500, weekly{week ? `, week to ${fmtDay(week)}` : ""}</span>
              <span className="flex gap-1" role="group" aria-label="Tail length">
                {TAILS.map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setTail(t)}
                    aria-pressed={tail === t}
                    className={`rounded px-1.5 py-0.5 ${tail === t ? "bg-line text-ink" : "hover:text-ink"}`}
                  >
                    {t}w
                  </button>
                ))}
              </span>
            </div>
            <div className="min-h-0 flex-1 lg:[container-type:size]">
              <div className="relative mx-auto aspect-square w-full max-w-[30rem] lg:h-[min(100cqw,100cqh)] lg:w-[min(100cqw,100cqh)] lg:max-w-none">
                <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="h-full w-full" role="img" aria-label="Sector rotation graph" data-testid="rotation-chart">
                  {/* quadrants, faintly tinted, named in their outer corners */}
                  {(
                    [
                      ["improving", PAD, PAD, "start", PAD + 6, PAD + 14],
                      ["leading", x(100), PAD, "end", SIZE - PAD - 6, PAD + 14],
                      ["lagging", PAD, y(100), "start", PAD + 6, SIZE - PAD - 8],
                      ["weakening", x(100), y(100), "end", SIZE - PAD - 6, SIZE - PAD - 8],
                    ] as const
                  ).map(([q, rx, ry, anchor, tx, ty]) => (
                    <g key={q}>
                      <rect x={rx} y={ry} width={inner / 2} height={inner / 2} fill={QUADRANT[q].color} opacity={0.07} />
                      <text x={tx} y={ty} textAnchor={anchor} className="fill-mute" fontSize={11} fontWeight={600}>
                        {QUADRANT[q].name}
                      </text>
                    </g>
                  ))}
                  {/* axes: ticks, the 100 lines, titles */}
                  {ticks(hx).map((v) => (
                    <text key={`x${v}`} x={x(v)} y={SIZE - PAD + 13} textAnchor="middle" className="fill-mute" fontSize={9}>
                      {v}
                    </text>
                  ))}
                  {ticks(hy).map((v) => (
                    <text key={`y${v}`} x={PAD - 4} y={y(v) + 3} textAnchor="end" className="fill-mute" fontSize={9}>
                      {v}
                    </text>
                  ))}
                  <line x1={x(100)} x2={x(100)} y1={PAD} y2={SIZE - PAD} stroke="#8a97b3" strokeOpacity={0.5} strokeDasharray="3 3" />
                  <line x1={PAD} x2={SIZE - PAD} y1={y(100)} y2={y(100)} stroke="#8a97b3" strokeOpacity={0.5} strokeDasharray="3 3" />
                  <text x={SIZE - PAD} y={SIZE - 3} textAnchor="end" className="fill-mute" fontSize={9}>
                    RS-Ratio →
                  </text>
                  <text x={10} y={PAD - 10} className="fill-mute" fontSize={9}>
                    ↑ RS-Momentum
                  </text>
                  {/* tails, then heads on top */}
                  {shown.map((r) => {
                    const dim = focus !== null && focus !== r.sector;
                    const color = QUADRANT[r.quadrant].color;
                    const pts = r.points.map((p) => `${x(p.ratio)},${y(p.momentum)}`).join(" ");
                    return (
                      <g key={r.sector} opacity={dim ? 0.15 : 1}>
                        <polyline points={pts} fill="none" stroke={color} strokeOpacity={0.55} strokeWidth={1.5} strokeLinejoin="round" />
                        {r.points.slice(0, -1).map((p) => (
                          <circle key={p.day} cx={x(p.ratio)} cy={y(p.momentum)} r={1.8} fill={color} fillOpacity={0.7} />
                        ))}
                      </g>
                    );
                  })}
                  {shown.map((r) => {
                    const p = r.points.at(-1);
                    if (!p) return null;
                    const dim = focus !== null && focus !== r.sector;
                    const cx = x(p.ratio);
                    const cy = y(p.momentum);
                    const lab = labels.get(r.sector);
                    return (
                      <g
                        key={r.sector}
                        opacity={dim ? 0.2 : 1}
                        onMouseEnter={() => setFocus(r.sector)}
                        onMouseLeave={() => setFocus(null)}
                        data-testid={`rotation-${r.sector}`}
                        style={{ cursor: "default" }}
                      >
                        <circle cx={cx} cy={cy} r={12} fill="transparent" />
                        <circle cx={cx} cy={cy} r={4.5} fill={QUADRANT[r.quadrant].color} stroke={SURFACE} strokeWidth={2} />
                        {lab && Math.abs(lab.ly - (cy + 3)) > 2 && (
                          // the label had to move off its dot: a thin line ties them
                          <line x1={cx} y1={cy} x2={lab.right ? lab.lx - 1 : lab.lx + 1} y2={lab.ly - 3} stroke="#8a97b3" strokeOpacity={0.6} strokeWidth={0.75} />
                        )}
                        {lab && (
                          <text x={lab.lx} y={lab.ly} textAnchor={lab.right ? "start" : "end"} className="fill-ink" fontSize={10}>
                            {short(r.sector)}
                          </text>
                        )}
                      </g>
                    );
                  })}
                </svg>
                {active && head && tipCorner && (
                  <div
                    role="tooltip"
                    data-corner={tipCorner}
                    className="pointer-events-none absolute z-10 rounded border border-line bg-panel/95 px-2 py-1 text-[11px] text-ink shadow"
                    style={{
                      [tipCorner.endsWith("l") ? "left" : "right"]: `${((PAD + 4) / SIZE) * 100}%`,
                      [tipCorner.startsWith("t") ? "top" : "bottom"]: `${((PAD + 20) / SIZE) * 100}%`,
                    }}
                  >
                    <p className="font-semibold">{active.sector}</p>
                    <p className="text-mute">
                      {active.index_name} · {QUADRANT[active.quadrant].name}
                    </p>
                    <p className="tabular-nums">
                      RS-Ratio {head.ratio.toFixed(2)} · RS-Momentum {head.momentum.toFixed(2)}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>
          <div
            className="grid min-w-0 shrink-0 grid-cols-1 content-start gap-x-4 gap-y-2 text-xs sm:grid-cols-2 lg:min-h-0 lg:grid-cols-1 lg:overflow-hidden lg:border-l lg:border-line lg:pl-3"
            aria-label="Sectors by quadrant"
          >
            {LIST_ORDER.map((q) => {
              const rows = shown.filter((r) => r.quadrant === q).sort((a, b) => (b.points.at(-1)?.ratio ?? 0) - (a.points.at(-1)?.ratio ?? 0));
              return (
                <section key={q} aria-label={QUADRANT[q].name}>
                  <h4 className="mb-0.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-mute">
                    <span className="inline-block h-2 w-2 rounded-full" style={{ background: QUADRANT[q].color }} aria-hidden="true" />
                    {QUADRANT[q].name} <span className="font-normal normal-case">({rows.length})</span>
                  </h4>
                  {rows.length === 0 ? (
                    <p className="pl-3.5 text-[11px] text-mute">None</p>
                  ) : (
                    <ul className="pl-3.5">
                      {rows.map((r) => {
                        const p = r.points.at(-1);
                        return (
                          <li
                            key={r.sector}
                            className={`flex justify-between gap-1 rounded px-1 ${focus === r.sector ? "bg-line" : ""}`}
                            onMouseEnter={() => setFocus(r.sector)}
                            onMouseLeave={() => setFocus(null)}
                          >
                            <span className="truncate text-ink" title={`${r.sector} (${r.index_name})`}>
                              {short(r.sector)}
                            </span>
                            <span className="shrink-0 text-[10px] tabular-nums text-mute" title="RS-Ratio / RS-Momentum">
                              {p ? `${p.ratio.toFixed(1)}/${p.momentum.toFixed(1)}` : "—"}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              );
            })}
            <p className="text-[11px] text-mute sm:col-span-2 lg:col-span-1">
              Sectors usually turn clockwise: Improving → Leading → Weakening → Lagging. RS-Ratio: the sector against NIFTY 500 vs its 10-week trend; RS-Momentum: that ratio vs 4
              weeks ago.
              {short_.length > 0 ? ` Shorter tails: ${short_.map((r) => short(r.sector)).join(", ")} (NSE started these indices recently).` : ""} Display only.
            </p>
          </div>
        </div>
      )}
    </Inner>
  );
}
