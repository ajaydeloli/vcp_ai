"use client";

import { useState } from "react";
import { Nav } from "./Nav";
import { SetupOverview } from "./SetupOverview";
import { StatusBar } from "./StatusBar";
import { StockPanel } from "./StockPanel";
import { HistoryCard, StrategiesCard } from "./StockExtras";
import { TopBar } from "./TopBar";
import { Card, Empty } from "./ui";

const open = (symbol: string) => window.location.assign(`/stocks/${encodeURIComponent(symbol)}`);

/** One stock in full: chart, setup details, Trend Template, score, other strategies, history. */
export function StockAnalysis({ symbol }: { symbol: string | null }) {
  const [strategy, setStrategy] = useState("vcp");
  return (
    <div className="flex min-h-screen">
      <Nav active="Stock Analysis" />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Stock analysis{symbol ? `: ${symbol}` : ""}</h1>
        <TopBar onPick={open} />
        {symbol === null ? (
          <Card title="Stock analysis">
            <Empty>Search a symbol or company above to analyse a stock.</Empty>
          </Card>
        ) : (
          <>
            <StockPanel key={symbol} selection={{ symbol, strategy }} onSelect={(s) => setStrategy(s.strategy)} onStockPage />
            <SetupOverview selection={{ symbol, strategy }} />
            <div className="grid gap-4 xl:grid-cols-2">
              <StrategiesCard symbol={symbol} strategy={strategy} onSelect={setStrategy} />
              <HistoryCard symbol={symbol} />
            </div>
          </>
        )}
      </main>
      <StatusBar />
    </div>
  );
}
