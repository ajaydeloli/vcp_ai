"use client";

import { useMarket } from "@/lib/api";
import { fmtPct } from "@/lib/fmt";
import { Card, Empty, ErrorBox, Loading } from "./ui";

export function MarketOverview() {
  const market = useMarket(250);
  const m = market.data;

  const last = m?.days[m.days.length - 1];

  return (
    <Card title="Market overview" subtitle={m?.label} className="h-full min-w-0">
      {market.isError ? (
        <ErrorBox error={market.error} />
      ) : market.isPending ? (
        <Loading what="market" />
      ) : !m || m.days.length === 0 ? (
        <Empty>No market data yet.</Empty>
      ) : (
        <div className="space-y-3 text-xs">
          <div className="grid grid-cols-3 gap-2">
            <div>
              <p className="text-mute">Regime</p>
              <p className={`text-base font-semibold ${last?.regime_on ? "text-up" : "text-down"}`}>
                {last?.regime_on ? "ON" : "OFF"}
              </p>
            </div>
            <div>
              <p className="text-mute">Breadth</p>
              <p className="text-base font-semibold">{fmtPct(last?.breadth_pct)}</p>
            </div>
            <div>
              <p className="text-mute">On, last 20</p>
              <p className="text-base font-semibold">{m.regime_days_on_last_20} / 20</p>
            </div>
          </div>
          <p className="text-mute">
            The regime is {m.regime_rule}: on when at least {m.breadth_threshold_pct}% of the universe closes above
            its 50-day average. The index and breadth charts are in Market health below.
          </p>
        </div>
      )}
    </Card>
  );
}
