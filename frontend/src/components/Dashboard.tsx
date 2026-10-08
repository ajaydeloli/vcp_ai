"use client";

import { ActivityPanel } from "./ActivityPanel";
import { MarketHealth } from "./MarketHealth";
import { Nav } from "./Nav";
import { PaperPanel } from "./PaperPanel";
import { SetupCounts } from "./SetupCounts";
import { StatusBar } from "./StatusBar";
import { Tiles } from "./Tiles";

/** The overall market view: index and scan tiles with the health score, market health, setup counts, paper trading, activity. */
export function Dashboard() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Dashboard</h1>
        <Tiles />
        <MarketHealth />
        <div className="grid gap-4 xl:grid-cols-12">
          <div className="min-w-0 xl:col-span-4 [&>section]:h-full">
            <SetupCounts />
          </div>
          <div className="xl:col-span-8 [&>section]:h-full">
            <PaperPanel />
          </div>
        </div>
        <div className="xl:relative xl:min-h-[360px]">
          <ActivityPanel />
        </div>
      </main>
      <StatusBar />
    </div>
  );
}
