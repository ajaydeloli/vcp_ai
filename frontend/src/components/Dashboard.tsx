"use client";

import { ActivityPanel } from "./ActivityPanel";
import { KpiCards } from "./KpiCards";
import { MarketHealth } from "./MarketHealth";
import { MarketStage } from "./MarketStage";
import { Nav } from "./Nav";
import { PaperPanel } from "./PaperPanel";
import { StatusBar } from "./StatusBar";
import { TopBar } from "./TopBar";

/** The overall market view: counts, market health, breadth, stage cycle, our index, best setups, activity, paper. */
export function Dashboard() {
  return (
    <div className="flex min-h-screen">
      <Nav />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Dashboard</h1>
        <TopBar onPick={(symbol) => window.location.assign(`/stocks/${encodeURIComponent(symbol)}`)} />
        <KpiCards />
        <MarketHealth />
        <div className="grid gap-4 xl:grid-cols-12">
          <div className="min-w-0 xl:col-span-5 [&>section]:h-full">
            <MarketStage />
          </div>
          <div className="xl:col-span-7 [&>section]:h-full">
            <PaperPanel />
          </div>
        </div>
        <ActivityPanel />
      </main>
      <StatusBar />
    </div>
  );
}
