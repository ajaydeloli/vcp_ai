"use client";

import { useState } from "react";
import { useBars, useStockSetups } from "@/lib/api";
import type { RangeId } from "@/lib/chartData";
import { fmtDay, fmtPct, fmtPrice, strategyLabel, tone } from "@/lib/fmt";
import { ChartView } from "./ChartView";
import { SetupDetails } from "./SetupDetails";
import type { Selection } from "./SetupsTable";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";

export function StockPanel({ selection }: { selection: Selection | null }) {
  if (!selection) {
    return (
      <Card title="Chart" className="min-w-0">
        <Empty>Select a stock from the list to see its chart and setup details.</Empty>
      </Card>
    );
  }
  return <StockView key={`${selection.symbol}:${selection.strategy}`} selection={selection} />;
}

function StockView({ selection }: { selection: Selection }) {
  const bars = useBars(selection.symbol);
  const stock = useStockSetups(selection.symbol);
  const [strategy, setStrategy] = useState(selection.strategy);
  const [range, setRange] = useState<RangeId>("1Y");

  const setups = stock.data?.setups ?? [];
  const active = setups.find((s) => s.strategy_id === strategy) ?? setups[0] ?? null;

  const list = bars.data?.bars ?? [];
  const last = list[list.length - 1];
  const prev = list[list.length - 2];
  const change =
    last?.close != null && prev?.close != null && prev.close !== 0
      ? (last.close / prev.close - 1) * 100
      : null;
  const company = bars.data?.company ?? stock.data?.company ?? null;

  return (
    <Card
      title={
        <span className="flex flex-wrap items-baseline gap-x-3">
          <span className="text-lg">{selection.symbol}</span>
          {company ? <span className="text-xs font-normal text-mute">{company}</span> : null}
        </span>
      }
      subtitle={
        last ? `Close ${fmtDay(last.day)}, adjusted prices` : bars.isPending ? undefined : "No price data"
      }
      right={
        <div className="text-right">
          <p className="text-xl font-semibold tabular-nums">{fmtPrice(last?.close)}</p>
          <p className={`text-xs tabular-nums ${tone(change)}`}>{fmtPct(change, 2, true)}</p>
        </div>
      }
      className="min-w-0"
    >
      {setups.length > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs" role="group" aria-label="Setup shown on the chart">
          {setups.map((s) => (
            <button
              key={s.strategy_id}
              type="button"
              aria-pressed={active?.strategy_id === s.strategy_id}
              onClick={() => setStrategy(s.strategy_id)}
              className={`rounded border px-2 py-1 ${
                active?.strategy_id === s.strategy_id
                  ? "border-accent bg-accent/10 text-ink"
                  : "border-line text-mute hover:text-ink"
              }`}
            >
              {strategyLabel(s.strategy_id)}
            </button>
          ))}
          {active ? (
            <span className="ml-1 flex items-center gap-1.5">
              <GradeBadge classification={active.classification} grade={active.grade} />
              <StatusPill status={active.status} />
            </span>
          ) : null}
        </div>
      ) : null}

      {bars.isError ? (
        <ErrorBox error={bars.error} />
      ) : bars.isPending ? (
        <Loading what="chart" />
      ) : list.length === 0 ? (
        <Empty>No price bars for {selection.symbol}.</Empty>
      ) : (
        <ChartView bars={list} setup={active} range={range} onRange={setRange} />
      )}

      <div className="mt-4">
        {stock.isError ? (
          <ErrorBox error={stock.error} />
        ) : stock.isPending ? (
          <Loading what="setup details" />
        ) : (
          <SetupDetails stock={stock.data} setup={active} />
        )}
      </div>
    </Card>
  );
}
