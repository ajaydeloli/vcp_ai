"use client";

// Setups and breakouts per session, all five strategies together, with the failure rate of
// recent breakouts (FRONTEND_SPECIFICATION §67.28, step 1). Display only.
import { useMemo } from "react";
import { fmtDay, fmtInt, fmtPct } from "@/lib/fmt";
import type { MarketHealth as Health } from "@/lib/schemas";
import { C, Figure, Inner, Legend } from "./MarketParts";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { type Pillar, pointsClass } from "./ScoreReadings";
import { Empty } from "./ui";

const FLOW_WINDOW = 20; // sessions behind each point of the failure line (api/health.py)
const FAIL_WITHIN = 5;

export function SetupFlowCard({ flow, box, pillar, judged }: { flow: Health["flow"]; box: string; pillar?: Pillar; judged?: number }) {
  const counts = useMemo<MiniSeries[]>(
    () => [
      { name: "Breakouts", color: C.up, kind: "bar", data: flow.map((p) => ({ time: p.day, value: p.breakouts })) },
      {
        name: "New setups",
        color: C.blue,
        data: flow.flatMap((p) => (p.new_setups === null ? [] : [{ time: p.day, value: p.new_setups }])),
      },
    ],
    [flow],
  );
  const failed = useMemo<MiniSeries[]>(
    () => [
      {
        name: "Failed breakouts %",
        color: C.down,
        data: flow.flatMap((p) => (p.failed_pct === null ? [] : [{ time: p.day, value: p.failed_pct }])),
      },
    ],
    [flow],
  );
  const last = flow[flow.length - 1];
  const held = pillar?.parts.find((x) => x.id === "held");
  const rate = last?.failed_pct ?? null;
  return (
    <Inner title="Setups and breakouts" box={box}>
      {!last ? (
        <Empty>No stored scans yet.</Empty>
      ) : (
        <div className="flex gap-4">
          <div className="min-w-0 flex-1">
            <MiniLineChart series={counts} height={130} plain whole label="setup-flow-chart" />
            <Legend
              items={[
                ["Breakouts per day", C.up],
                ["New setups per day", C.blue],
              ]}
            />
            {failed[0]?.data.length ? (
              <MiniLineChart series={failed} height={95} plain threshold={{ value: 50, title: "50%" }} label="failed-breakouts-chart" />
            ) : (
              <p className="py-6 text-center text-[11px] text-mute">Failure rate: too few breakouts judged yet.</p>
            )}
            <Legend
              items={[
                [`Failed within ${FAIL_WITHIN} sessions, last ${FLOW_WINDOW} sessions`, C.down],
                ["dashed: 50%", C.warn],
              ]}
            />
            <p className="mt-1 text-[11px] text-mute">
              Our five strategies&apos; own breakouts since {fmtDay(flow[0]?.day)}, the first stored scan; the score&apos;s figure uses a price rule back to 2022.
            </p>
          </div>
          <aside aria-label="Setup flow figures" className="w-44 shrink-0 space-y-2 border-l border-line pl-4">
            <Figure label="Breakouts" value={fmtInt(last.breakouts)} sub="today" color="text-up" small />
            <div className="border-t border-line pt-2">
              <Figure label="New setups" value={last.new_setups === null ? "—" : fmtInt(last.new_setups)} sub="today" color="text-accent" small />
            </div>
            <div
              className="border-t border-line pt-2"
              title={`Breakouts that closed back below their pivot within ${FAIL_WITHIN} sessions, of those judged in the last ${FLOW_WINDOW} sessions`}
            >
              <Figure
                label="Failed breakouts"
                value={rate === null ? "—" : fmtPct(rate, 0)}
                sub={`(${last.judged} judged)`}
                color={rate === null ? "text-ink" : rate > 50 ? "text-down" : rate > 25 ? "text-warn" : "text-up"}
              small
              />
            </div>
            {/* the score's breakout environment: one price rule over all history (MARKET_HEALTH_SPECIFICATION §2) */}
            <div className="border-t border-line pt-2" title="The Market Health Score's breakouts (a price rule back to 2022): held their pivot for 5 sessions">
              <Figure
                label="Held 5 sessions (score)"
                value={held?.reading === null || held?.reading === undefined ? "—" : fmtPct(held.reading, 0)}
                sub={judged ? `price rule, ${judged} judged` : "price rule"}
                color={pointsClass(held?.points ?? null)}
              small
              />
            </div>
          </aside>
        </div>
      )}
    </Inner>
  );
}
