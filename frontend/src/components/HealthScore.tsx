"use client";

// Market Health Score 2 (MARKET_HEALTH_SPECIFICATION): the raw score and the label after the caps,
// the five pillars and what each adds, and the history. Display and research only; the traffic
// light is logged in shadow and moves no trade.
import { useMemo, useState } from "react";
import { fmtDay } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { BOX, EXPOSURE, Inner } from "./MarketParts";
import { ScoreGauge } from "./ScoreGauge";

type Score = NonNullable<Health["score"]>;
type Pillar = Score["pillars"][number];

const RANGES = [
  { id: "3M", sessions: 63 },
  { id: "6M", sessions: 126 },
  { id: "12M", sessions: 252 },
] as const;
const BANDS = [
  { value: 80, title: "Strong" },
  { value: 65, title: "Constructive" },
  { value: 50, title: "Mixed" },
  { value: 35, title: "Weak" },
];
const WEAK = 50; // a pillar below this is highlighted

function barColor(score: number): string {
  return score >= 65 ? "#26c281" : score >= WEAK ? "#f5a524" : "#ef5350";
}

function fmtReading(v: number | null): string {
  if (v === null) return "—";
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
}

function PillarRow({ p }: { p: Pillar }) {
  const weak = p.score !== null && p.score < WEAK;
  return (
    <details className={`rounded px-2 py-1 ${weak ? "bg-down/10 ring-1 ring-down/40" : ""}`} data-testid={`pillar-${p.id}`}>
      <summary className="flex cursor-pointer list-none items-center gap-2 text-xs">
        <span className="w-44 shrink-0 truncate text-ink" title={p.title}>
          {p.title} <span className="text-mute">{p.weight}%</span>
        </span>
        <span className="relative h-2 min-w-0 flex-1 rounded bg-line" aria-hidden="true">
          {p.score !== null && <span className="absolute inset-y-0 left-0 rounded" style={{ width: `${p.score}%`, background: barColor(p.score) }} />}
        </span>
        <span className="w-9 shrink-0 text-right tabular-nums text-ink">{p.score === null ? "—" : Math.round(p.score)}</span>
        <span className="w-12 shrink-0 text-right tabular-nums text-mute" title="Points this pillar adds to the raw score">
          {p.contribution === null ? "missing" : `+${p.contribution.toFixed(1)}`}
        </span>
      </summary>
      {p.note && <p className="mt-1 text-[11px] text-mute">{p.note}</p>}
      {p.parts.length > 0 && (
        <table className="mt-1 w-full text-[11px]">
          <tbody>
            {p.parts.map((x) => (
              <tr key={x.id} className="text-mute">
                <td className="py-0.5 pr-2">{x.label}</td>
                <td className="py-0.5 pr-2 text-right tabular-nums text-ink">{fmtReading(x.reading)}</td>
                <td className="py-0.5 text-right tabular-nums">{x.points === null ? "—" : `${Math.round(x.points)} pts`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}

function History({ history }: { history: Score["history"] }) {
  const [range, setRange] = useState<(typeof RANGES)[number]["id"]>("6M");
  const series = useMemo((): MiniSeries[] => {
    const n = RANGES.find((r) => r.id === range)?.sessions ?? 126;
    const shown = history.slice(-n);
    const data = shown.flatMap((h) => (h.raw === null ? [] : [{ time: h.day, value: h.raw }]));
    const markers = shown.filter((h) => h.thrust).map((h) => ({ time: h.day, color: "#26c281", text: "Thrust", position: "belowBar" as const }));
    return [{ name: "Raw score", color: "#60a5fa", data, markers }];
  }, [history, range]);
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between">
        <p className="text-xs text-mute">Raw score</p>
        <div className="flex gap-1" role="group" aria-label="History range">
          {RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setRange(r.id)}
              aria-pressed={range === r.id}
              className={`rounded px-1.5 py-0.5 text-[11px] ${range === r.id ? "bg-line text-ink" : "text-mute hover:text-ink"}`}
            >
              {r.id}
            </button>
          ))}
        </div>
      </div>
      <MiniLineChart series={series} height={210} threshold={BANDS} label="market-health-score-history" plain whole />
    </div>
  );
}

/** The Market Health Score card: raw score and capped label, pillars, caps, history. */
export function HealthScoreCard({ score }: { score: Health["score"] }) {
  if (!score) {
    return (
      <Inner title="Market health score" box={BOX.score}>
        <p className="text-sm text-mute">No score stored yet: it is computed by the daily run (vcp health build).</p>
      </Inner>
    );
  }
  const light = score.exposure ? EXPOSURE[score.exposure] : undefined;
  const capped = score.label !== score.raw_label;
  return (
    <Inner title="Market health score" box={BOX.score}>
      <div className="grid gap-4 lg:grid-cols-[13rem_minmax(0,1fr)_50%]">
        <div className="space-y-2">
          <ScoreGauge raw={score.raw} label={score.label} />
          <p className="text-center text-[11px] text-mute">
            Raw score {score.raw === null ? "—" : Math.round(score.raw)}, {score.raw_label ?? "—"}
            {capped ? "; the label is capped (see the caps)" : ""}
          </p>
          {light && (
            <p className="flex items-center gap-1.5 text-xs text-ink" title="Shadow traffic light: logged next to the paper ledger, no trading effect">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: light.color }} aria-hidden="true" />
              Shadow light: {light.text}
            </p>
          )}
          <p className="text-[11px] text-mute">As of {fmtDay(score.day)}</p>
          {score.last_thrust && <p className="text-[11px] text-up">Breadth thrust on {fmtDay(score.last_thrust)}</p>}
        </div>
        <div className="min-w-0 space-y-1" aria-label="Pillars">
          {score.pillars.map((p) => (
            <PillarRow key={p.id} p={p} />
          ))}
          <p className="pt-1 text-[11px] text-mute">Open a pillar for its readings. Highlighted: below {WEAK}. Display and research only.</p>
          {score.caps.length > 0 && (
            // side by side, so the caps do not make the card taller
            <ul aria-label="Caps" className="grid gap-x-4 gap-y-1 pt-1 text-[11px] sm:grid-cols-2">
              {score.caps.map((c) => (
                <li key={c.id} className="text-warn">
                  At most {c.max}: <span className="text-mute">{c.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <History history={score.history} />
      </div>
    </Inner>
  );
}
