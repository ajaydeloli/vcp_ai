"use client";

import { useMemo } from "react";
import { useMarket } from "@/lib/api";
import { fmtPct } from "@/lib/fmt";
import { MiniLineChart, type MiniSeries } from "./MiniLineChart";
import { Card, Empty, ErrorBox, Loading } from "./ui";

export function MarketOverview() {
  const market = useMarket(250);
  const m = market.data;

  const index = useMemo<MiniSeries[]>(() => {
    if (!m) return [];
    const pick = (key: "index" | "index_ma50") =>
      m.days.flatMap((d) => (d[key] === null ? [] : [{ time: d.day, value: d[key] as number }]));
    return [
      { name: "Index", color: "#4aa3ff", data: pick("index") },
      { name: "50-day average", color: "#f5a524", data: pick("index_ma50") },
    ];
  }, [m]);

  const breadth = useMemo<MiniSeries[]>(() => {
    if (!m) return [];
    return [
      {
        name: "Breadth %",
        color: "#26c281",
        data: m.days.flatMap((d) => (d.breadth_pct === null ? [] : [{ time: d.day, value: d.breadth_pct }])),
      },
    ];
  }, [m]);

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
          <div>
            <p className="mb-1 text-mute">Universe index vs its 50-day average (100 = first day shown)</p>
            <MiniLineChart series={index} label="universe-index" />
          </div>
          <div>
            <p className="mb-1 text-mute">
              Share of the universe above its 50-day average (rule {m.regime_rule}: on at ≥{" "}
              {m.breadth_threshold_pct}%)
            </p>
            <MiniLineChart
              series={breadth}
              height={90}
              threshold={{ value: m.breadth_threshold_pct, title: `${m.breadth_threshold_pct}%` }}
              label="breadth"
            />
          </div>
        </div>
      )}
    </Card>
  );
}
