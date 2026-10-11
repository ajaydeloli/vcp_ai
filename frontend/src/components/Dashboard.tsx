"use client";

import { ActivityPanel } from "./ActivityPanel";
import { MarketHealth } from "./MarketHealth";
import { Nav } from "./Nav";
import { LiveNotice } from "./Live";
import { StatusBar } from "./StatusBar";
import { Tiles } from "./Tiles";

/** The overall market view: index and scan tiles with the health score, market health, setup counts, activity. */
export function Dashboard() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Market Overview</h1>
        <LiveNotice />
        <Tiles />
        <MarketHealth />
        {/* paper trading has its own page (/paper); the feedback-loop card keeps its market reading */}
        <ActivityPanel />
      </main>
      <StatusBar />
    </div>
  );
}
