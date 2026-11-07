"use client";

import { useStrategies, useSummary } from "@/lib/api";
import { fmtInt, fmtNum, strategyLabel } from "@/lib/fmt";
import { Page } from "./Page";
import { Card, ErrorBox, GradeBadge, Loading } from "./ui";

/** The frozen strategy: its grade tiers and today's counts. */
export function Strategies() {
  const strategies = useStrategies();
  const summary = useSummary();

  return (
    <Page active="Strategies" title="Strategies">
      {strategies.isError ? (
        <ErrorBox error={strategies.error} />
      ) : strategies.isPending ? (
        <Loading what="strategies" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {strategies.data.strategies.map((s) => {
            const c = summary.data?.strategies.find((x) => x.strategy_id === s.strategy_id);
            return (
              <Card
                key={s.strategy_id}
                title={strategyLabel(s.strategy_id)}
                subtitle={`Stage ${s.stage} · algorithm ${s.algorithm_version} · config ${s.config_hash.slice(0, 8)}`}
              >
                <div className="grid grid-cols-3 divide-x divide-line text-center">
                  {[
                    ["Ranked", fmtInt(c?.ranked)],
                    ["Grade 2+", fmtInt(c?.grade2_plus)],
                    ["Breakouts", fmtInt(c?.breakouts)],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <p className="text-[11px] text-mute">{k}</p>
                      <p className="text-base font-semibold tabular-nums text-ink">{v}</p>
                    </div>
                  ))}
                </div>
                <p className="mt-3 border-t border-line pt-3 text-[11px] uppercase text-mute">
                  Grade tiers (minimum grade {fmtNum(s.min_grade, 0)})
                </p>
                <ul className="mt-1 space-y-1 text-xs">
                  {s.tiers.map((t) => (
                    <li key={t.name} className="flex items-center justify-between gap-2">
                      <GradeBadge classification={t.name} grade={t.grade} />
                      <span className="text-mute">{t.ranked ? "ranked" : "not ranked"}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            );
          })}
        </div>
      )}

    </Page>
  );
}
