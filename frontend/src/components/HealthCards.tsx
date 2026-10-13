"use client";

// The Market Health Score's pillars as cards (FRONTEND_SPECIFICATION §67.35): one chart and four
// or five headline figures each, like the Market stage card. The figures are the score's own
// readings, coloured by their points; the full list of readings is on the score card.
import { type ReactNode, useMemo } from "react";
import { fmtDay } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { BOX, C, Figure, Inner, Legend, NSE } from "./MarketParts";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { type Pillar, pointsClass } from "./ScoreReadings";

type SP = Health["score_points"][number];
type Part = Pillar["parts"][number];

const CHART_H = 250;

function line(name: string, color: string, pts: SP[], pick: (p: SP) => number | null): MiniSeries {
  const data: { time: string; value: number }[] = [];
  for (const p of pts) {
    const v = pick(p);
    if (v !== null) data.push({ time: p.day, value: v });
  }
  return { name, color, data };
}

const part = (p: Pillar | undefined, id: string): Part | undefined => p?.parts.find((x) => x.id === id);
const signed = (v: number | null | undefined, digits = 1, unit = "") => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}${unit}`);

/** The column of headline figures beside a chart, divided like the Market stage card. */
function Figures({ label, items }: { label: string; items: { label: string; value: string; sub?: string; points: number | null | undefined; title?: string }[] }) {
  return (
    <aside aria-label={label} className="w-44 shrink-0 space-y-2 border-l border-line pl-4">
      {items.map((f, i) => (
        <div key={f.label} className={i ? "border-t border-line pt-2" : ""} title={f.title}>
          <Figure label={f.label} value={f.value} sub={f.sub} color={pointsClass(f.points ?? null)} small />
        </div>
      ))}
    </aside>
  );
}

function Card({ title, box, children }: { title: string; box: string; children: ReactNode }) {
  return (
    <Inner title={title} box={box}>
      <div className="flex gap-4">{children}</div>
    </Inner>
  );
}

/** NIFTY 500 against its 50, 150 and 200-day averages (pillar A). */
export function IndexTrendCard({ pts, pillar }: { pts: SP[]; pillar: Pillar | undefined }) {
  const series = useMemo(
    () => [
      line("NIFTY 500", NSE.nifty500, pts, (p) => p.nifty500),
      line("50-day", C.warn, pts, (p) => p.n500_ma50),
      line("150-day", C.violet, pts, (p) => p.n500_ma150),
      line("200-day", C.down, pts, (p) => p.n500_ma200),
    ],
    [pts],
  );
  const a50 = part(pillar, "above_50");
  const a200 = part(pillar, "above_200");
  const slope = part(pillar, "rising_200");
  const n50 = part(pillar, "nifty50");
  const small = part(pillar, "smallcap250");
  const yes = (x: Part | undefined) => (x?.reading === null || x?.reading === undefined ? "—" : x.reading >= 1 ? "Yes" : "No");
  const confirms = [n50, small].filter((x) => (x?.reading ?? 0) >= 1).length;
  return (
    <Card title="Index trend" box={BOX.index}>
      <div className="min-w-0 flex-1">
        <MiniLineChart series={series} height={CHART_H} plain label="index-price-chart" />
        <Legend
          items={[
            ["NIFTY 500", NSE.nifty500],
            ["its 50-day", C.warn],
            ["its 150-day", C.violet],
            ["its 200-day", C.down],
          ]}
        />
      </div>
      <Figures
        label="Index figures"
        items={[
          { label: "NIFTY 500 vs 50-day", value: signed(a50?.reading, 1, "%"), points: a50?.points },
          { label: "NIFTY 500 vs 200-day", value: signed(a200?.reading, 1, "%"), points: a200?.points },
          {
            label: "200-day average",
            value: slope?.reading === null || slope?.reading === undefined ? "—" : slope.reading > 0 ? "Rising" : "Falling",
            sub: `${signed(slope?.reading, 2, "%")} in 20 sessions`,
            points: slope?.points,
          },
          {
            label: "Confirmation",
            value: `${confirms} of 2`,
            sub: `NIFTY 50 ${yes(n50)} · Smallcap 250 ${yes(small)}`,
            points: confirms === 2 ? 100 : confirms === 1 ? 50 : 0,
            title: "NIFTY 50 and Smallcap 250 above their own 50-day averages",
          },
        ]}
      />
    </Card>
  );
}

/** NIFTY 500 distribution and accumulation days (pillar D). */
export function VolumeCard({ pts, pillar }: { pts: SP[]; pillar: Pillar | undefined }) {
  const series = useMemo(
    () => [
      line("Distribution days (25)", C.down, pts, (p) => p.dist25),
      line("Accumulation days (25)", C.up, pts, (p) => p.acc25),
      {
        name: "Volume days",
        color: C.up,
        kind: "dots" as const,
        data: pts.flatMap((p) =>
          p.kind === "accumulation" ? [{ time: p.day, value: 0, color: C.up }] : p.kind === "distribution" ? [{ time: p.day, value: 0, color: C.down }] : [],
        ),
      },
    ],
    [pts],
  );
  const last = pts[pts.length - 1];
  const dist = part(pillar, "distribution");
  const cluster = part(pillar, "clustering");
  const acc = part(pillar, "accumulation_days");
  const value = part(pillar, "up_down_value");
  return (
    <Card title="Accumulation / distribution" box={BOX.volume}>
      <div className="min-w-0 flex-1">
        <MiniLineChart series={series} height={CHART_H} plain whole label="volume-days-chart" />
        <Legend
          items={[
            ["distribution days in 25 sessions", C.down],
            ["accumulation days in 25 sessions", C.up],
          ]}
        />
        <p className="text-[11px] text-mute">Dots: NIFTY 500 down / up 0.2%+ on higher volume.</p>
      </div>
      <Figures
        label="Volume figures"
        items={[
          {
            label: "Distribution days",
            value: last?.dist25 === null || last?.dist25 === undefined ? "—" : String(last.dist25),
            sub: "NIFTY 500, last 25 sessions",
            points: dist?.points,
          },
          {
            label: "In the last 10 sessions",
            value: cluster?.reading === null || cluster?.reading === undefined ? "—" : String(cluster.reading),
            sub: "5 or more caps the label",
            points: cluster?.points,
          },
          { label: "Accumulation days", value: last?.acc25 === null || last?.acc25 === undefined ? "—" : String(last.acc25), sub: "last 25 sessions", points: acc?.points },
          {
            label: "Up / down traded value",
            value: value?.reading === null || value?.reading === undefined ? "—" : value.reading.toFixed(2),
            sub: "advancers vs decliners, 10 sessions",
            points: value?.points,
          },
        ]}
      />
    </Card>
  );
}

/** The share of our stocks above their own averages (pillar B, the trend half). */
export function BreadthCard({ pts, pillar, regime }: { pts: SP[]; pillar: Pillar | undefined; regime: ReactNode }) {
  const series = useMemo(
    () => [
      line("Above 50-day %", C.blue, pts, (p) => p.above50),
      line("Above 150-day %", C.warn, pts, (p) => p.above150),
      line("Above 200-day %", C.violet, pts, (p) => p.above200),
    ],
    [pts],
  );
  const pct = (x: Part | undefined) => (x?.reading === null || x?.reading === undefined ? "—" : `${x.reading.toFixed(1)}%`);
  const a50 = part(pillar, "above_50");
  const a150 = part(pillar, "above_150");
  const a200 = part(pillar, "above_200");
  const change = part(pillar, "change");
  return (
    <Card title="Breadth" box={BOX.breadth}>
      <div className="min-w-0 flex-1">
        <MiniLineChart
          series={series}
          height={CHART_H}
          plain
          threshold={[
            { value: 30, title: "30%" },
            { value: 70, title: "70%" },
          ]}
          label="breadth-chart"
        />
        <Legend
          items={[
            ["% above 50-day", C.blue],
            ["% above 150-day", C.warn],
            ["% above 200-day", C.violet],
            ["dashed: 30% and 70%", C.warn],
          ]}
        />
      </div>
      <aside aria-label="Breadth figures" className="w-44 shrink-0 space-y-2 border-l border-line pl-4">
        {[
          { label: "Above 50-day", value: pct(a50), points: a50?.points },
          { label: "Above 150-day", value: pct(a150), points: a150?.points },
          { label: "Above 200-day", value: pct(a200), points: a200?.points },
          { label: "Change in 10 sessions", value: signed(change?.reading, 1, " pp"), points: change?.points },
        ].map((f, i) => (
          <div key={f.label} className={i ? "border-t border-line pt-2" : ""}>
            <Figure label={f.label} value={f.value} color={pointsClass(f.points ?? null)} small />
          </div>
        ))}
        {regime}
      </aside>
    </Card>
  );
}

/** How many stocks take part: advancers, new highs and the average stock vs the index (pillar B,
 *  the participation half), with the breadth thrust. */
export function ParticipationCard({ pts, pillar, lastThrust }: { pts: SP[]; pillar: Pillar | undefined; lastThrust: string | null | undefined }) {
  const series = useMemo(
    () => [
      {
        ...line("Advancers share, 10-day", C.blue, pts, (p) => p.thrust_ratio),
        markers: pts.filter((p) => p.thrust).map((p) => ({ time: p.day, color: C.up, position: "aboveBar" as const, text: "thrust" })),
      },
    ],
    [pts],
  );
  const ad = part(pillar, "ad_trend");
  const hl = part(pillar, "highs_lows");
  const gap = part(pillar, "vqi_gap");
  return (
    <Card title="Participation" box={BOX.participation}>
      <div className="min-w-0 flex-1">
        <MiniLineChart
          series={series}
          height={CHART_H}
          plain
          threshold={[
            { value: 40, title: "40%" },
            { value: 61.5, title: "61.5%" },
          ]}
          label="thrust-chart"
        />
        <Legend
          items={[
            ["advancers / (advancers + decliners), 10-day average, %", C.blue],
            ["dashed: 40% and 61.5% (breadth thrust)", C.warn],
          ]}
        />
        <p className="text-[11px] text-mute" data-testid="thrust-note">
          {lastThrust
            ? `Last breadth thrust ${fmtDay(lastThrust)}: from below 40% to above 61.5% within 10 sessions.`
            : "No breadth thrust in the last year (from below 40% to above 61.5% within 10 sessions)."}
        </p>
      </div>
      <Figures
        label="Participation figures"
        items={[
          {
            label: "Net advances",
            value: ad?.reading === null || ad?.reading === undefined ? "—" : signed(ad.reading * 100, 1, "%"),
            sub: "of all moves, 20 sessions",
            points: ad?.points,
          },
          {
            label: "New highs share",
            value: hl?.reading === null || hl?.reading === undefined ? "—" : `${(hl.reading * 100).toFixed(0)}%`,
            sub: "of highs + lows, 10-day",
            points: hl?.points,
          },
          { label: "VQI vs NIFTY 500", value: signed(gap?.reading, 1, " pp"), sub: "the average stock, 1 month", points: gap?.points },
          {
            label: "Breadth thrust",
            value: lastThrust ? fmtDay(lastThrust) : "None",
            sub: "last signal in a year",
            points: lastThrust ? 100 : null,
            title: "An event, not part of the score",
          },
        ]}
      />
    </Card>
  );
}

/** Leaders and the Trend Template (pillar C); today's new highs have their own card (NewHighs.tsx). */
export function LeadershipCard({ pts, pillar }: { pts: SP[]; pillar: Pillar | undefined }) {
  const series = useMemo(() => [line("Trend Template %", C.up, pts, (p) => p.tt_share)], [pts]);
  const last = pts[pts.length - 1];
  const tt = part(pillar, "tt_share");
  const change = part(pillar, "leader_change");
  const h52 = part(pillar, "highs_52");
  const lead = part(pillar, "leaders_vs_vqi");
  return (
    <Card title="Leadership" box={BOX.leadership}>
      <div className="min-w-0 flex-1">
        <MiniLineChart
          series={series}
          height={CHART_H}
          plain
          threshold={[
            { value: 3, title: "3" },
            { value: 20, title: "20" },
          ]}
          label="trend-template-chart"
        />
        <Legend
          items={[
            ["stocks passing the Trend Template, %", C.up],
            ["dashed: 3% and 20%", C.warn],
          ]}
        />
      </div>
      <Figures
        label="Leadership figures"
        items={[
          { label: "Trend Template", value: tt?.reading === null || tt?.reading === undefined ? "—" : `${tt.reading.toFixed(1)}%`, sub: "of our stocks pass", points: tt?.points },
          {
            label: "Leaders",
            value: last?.leaders === null || last?.leaders === undefined ? "—" : String(last.leaders),
            sub: `TT + RS 80; ${signed(change?.reading, 0, "%")} in 20 sessions`,
            points: change?.points,
          },
          {
            label: "At a 52-week high",
            value: h52?.reading === null || h52?.reading === undefined ? "—" : `${h52.reading.toFixed(1)}%`,
            sub: "of our stocks, 10-day",
            points: h52?.points,
          },
          { label: "Leaders vs VQI", value: signed(lead?.reading, 1, " pp"), sub: "leaders of 20 sessions ago", points: lead?.points },
        ]}
      />
    </Card>
  );
}
