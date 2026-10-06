"use client";

import { useState } from "react";
import { useSetups, useStrategies, useSummary } from "@/lib/api";
import { ActivityPanel } from "./ActivityPanel";
import { KpiCards } from "./KpiCards";
import { MarketOverview } from "./MarketOverview";
import { Nav } from "./Nav";
import { PaperPanel } from "./PaperPanel";
import { SetupsTable, type Selection } from "./SetupsTable";
import { StatusBar } from "./StatusBar";
import { StockPanel } from "./StockPanel";
import { TopBar } from "./TopBar";
import { ErrorBox, Loading } from "./ui";

export function Dashboard() {
  const strategies = useStrategies();
  const summary = useSummary();
  const [picked, setPicked] = useState<Selection | null>(null);

  // until the user picks something, show the best-ranked VCP setup (same query as the table)
  const firstVcp = useSetups("vcp");
  const top = firstVcp.data?.rows[0];
  const selected: Selection | null =
    picked ?? (top ? { symbol: top.symbol, strategy: top.strategy_id } : null);

  return (
    <div className="flex min-h-screen">
      <Nav />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Dashboard</h1>
        <TopBar onPick={(symbol) => setPicked({ symbol, strategy: "vcp" })} />
        <KpiCards />
        {strategies.isError ? (
          <ErrorBox error={strategies.error} />
        ) : strategies.isPending ? (
          <Loading what="strategies" />
        ) : (
          <div className="grid gap-4 xl:grid-cols-12">
            <div className="min-w-0 xl:col-span-5">
              <SetupsTable
                strategies={strategies.data.strategies}
                summary={summary.data}
                selected={selected}
                onSelect={setPicked}
              />
            </div>
            <div className="min-w-0 xl:col-span-7">
              <StockPanel selection={selected} />
            </div>
          </div>
        )}
        <div className="grid gap-4 xl:grid-cols-12">
          <div className="xl:col-span-3">
            <ActivityPanel />
          </div>
          <div className="xl:col-span-4">
            <MarketOverview />
          </div>
          <div className="xl:col-span-5">
            <PaperPanel />
          </div>
        </div>
      </main>
      <StatusBar />
    </div>
  );
}
