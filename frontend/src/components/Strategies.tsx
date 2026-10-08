"use client";

import { useOverlap, useStrategies, useSummary } from "@/lib/api";
import { DASH, fmtInt, fmtNum, strategyLabel } from "@/lib/fmt";
import { Page } from "./Page";
import { Card, Empty, ErrorBox, GradeBadge, Loading, TINT } from "./ui";

const BOXES = [TINT.blue, TINT.green, TINT.violet, TINT.amber, TINT.cyan, TINT.pink] as const;

/** The five frozen strategies: their grade tiers, today's counts, and the stocks several share. */
export function Strategies() {
  const strategies = useStrategies();
  const summary = useSummary();
  const overlap = useOverlap();
  const shared = (overlap.data?.rows ?? []).filter((r) => r.strategies.length > 1);

  return (
    <Page active="Strategies" title="Strategies">
      {strategies.isError ? (
        <ErrorBox error={strategies.error} />
      ) : strategies.isPending ? (
        <Loading what="strategies" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {strategies.data.strategies.map((s, i) => {
            const c = summary.data?.strategies.find((x) => x.strategy_id === s.strategy_id);
            return (
              <Card
                key={s.strategy_id}
                title={strategyLabel(s.strategy_id)}
                subtitle={`Stage ${s.stage} · algorithm ${s.algorithm_version} · config ${s.config_hash.slice(0, 8)}`}
                box={BOXES[i % BOXES.length]}
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

      <Card
        title="Stocks in more than one strategy"
        subtitle={overlap.data?.as_of ? `Scan of ${overlap.data.as_of}` : undefined}
        box={TINT.orange}
      >
        {overlap.isError ? (
          <ErrorBox error={overlap.error} />
        ) : overlap.isPending ? (
          <Loading what="overlap" />
        ) : shared.length === 0 ? (
          <Empty>No stock is in more than one strategy today.</Empty>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-[11px] uppercase text-mute">
              <tr>
                <th className="px-2 py-2 text-left font-medium">Symbol</th>
                <th className="px-2 py-2 text-center font-medium">Strategies</th>
                <th className="px-2 py-2 text-left font-medium">Where</th>
              </tr>
            </thead>
            <tbody>
              {shared.map((r) => (
                <tr key={r.instrument_id} className="border-t border-line/60">
                  <td className="px-2 py-2">
                    <a href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium text-ink hover:text-accent">
                      {r.symbol}
                    </a>
                    <span className="block text-[11px] text-mute">{r.company ?? DASH}</span>
                  </td>
                  <td className="px-2 py-2 text-center tabular-nums">{r.strategies.length}</td>
                  <td className="px-2 py-2">
                    <span className="flex flex-wrap gap-1.5">
                      {r.strategies.map((x) => (
                        <span key={x.strategy_id} className="flex items-center gap-1 text-mute">
                          {strategyLabel(x.strategy_id)}
                          <GradeBadge classification={x.classification} grade={x.grade} />
                        </span>
                      ))}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </Page>
  );
}
