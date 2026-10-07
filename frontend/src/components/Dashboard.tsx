"use client";

import { useState } from "react";
import { useSetups, useStrategies } from "@/lib/api";
import { ActivityPanel } from "./ActivityPanel";
import { KpiCards } from "./KpiCards";
import { MarketOverview } from "./MarketOverview";
import { Nav } from "./Nav";
import { PaperPanel } from "./PaperPanel";
import { SetupOverview } from "./SetupOverview";
import { SetupsTable, type Selection } from "./SetupsTable";
import { StatusBar } from "./StatusBar";
import { StockPanel } from "./StockPanel";
import { TopBar } from "./TopBar";
import { ErrorBox, Loading } from "./ui";

export function Dashboard() {
  const strategies = useStrategies();
  // /dashboard?symbol=X (a link from the screener) opens that stock; read once, in the browser
  const [picked, setPicked] = useState<Selection | null>(() => {
    const symbol =
      typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("symbol");
    return symbol ? { symbol, strategy: "vcp" } : null;
  });

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
            <div className="min-w-0 xl:col-span-6 [&>section]:h-full">
              <SetupsTable strategies={strategies.data.strategies} selected={selected} onSelect={setPicked} />
            </div>
            <div className="min-w-0 xl:col-span-6 [&>section]:h-full">
              <StockPanel selection={selected} onSelect={setPicked} />
            </div>
          </div>
        )}
        <SetupOverview selection={selected} />
        <div className="grid gap-4 xl:grid-cols-12">
          <div className="xl:relative xl:col-span-3 xl:min-h-[420px]">
            <ActivityPanel />
          </div>
          <div className="xl:col-span-4 [&>section]:h-full">
            <MarketOverview />
          </div>
          <div className="xl:col-span-5 [&>section]:h-full">
            <PaperPanel />
          </div>
        </div>
      </main>
      <StatusBar />
    </div>
  );
}
