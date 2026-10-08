"use client";

import { useState } from "react";
import { useSetups } from "@/lib/api";
import { Nav } from "./Nav";
import { SetupOverview } from "./SetupOverview";
import { StatusBar } from "./StatusBar";
import { StockPanel } from "./StockPanel";
import { HistoryCard, StrategiesCard } from "./StockExtras";
import { Card, Empty } from "./ui";

const open = (symbol: string) => window.location.assign(`/stocks/${encodeURIComponent(symbol)}`);

/** One stock in full (the best VCP setup when no symbol is given): chart, setup details, Trend Template, score, other strategies, history. */
export function StockAnalysis({ symbol: given }: { symbol: string | null }) {
  const [strategy, setStrategy] = useState("vcp");
  // with no symbol in the address, open the best-ranked VCP setup (same query as the dashboard)
  const top = useSetups("vcp", undefined, given === null);
  const symbol = given ?? top.data?.rows[0]?.symbol ?? null;
  return (
    <div className="flex min-h-screen flex-col">
      <Nav active="Stock Analysis" onPick={open} />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">Stock analysis{symbol ? `: ${symbol}` : ""}</h1>
        {symbol === null ? (
          <Card title="Stock analysis">
            <Empty>
              {top.isPending
                ? "Loading the best VCP setup…"
                : "Search a symbol or company above to analyse a stock."}
            </Empty>
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
