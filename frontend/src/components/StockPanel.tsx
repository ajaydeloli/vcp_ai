"use client";

import { useState } from "react";
import { useBars, useLiveQuotes, useSetups, useStockSetups } from "@/lib/api";
import type { RangeId } from "@/lib/chartData";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, strategyLabel, tone } from "@/lib/fmt";
import { LiveLine } from "./Live";
import { WatchStar } from "./WatchStar";
import { ChartView } from "./ChartView";
import type { Selection } from "@/lib/selection";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";

export function StockPanel({
  selection,
  onSelect,
  onStockPage = false,
}: {
  selection: Selection | null;
  onSelect: (s: Selection) => void;
  /** true on the Stock Analysis page itself, which needs no link to itself */
  onStockPage?: boolean;
}) {
  if (!selection) {
    return (
      <Card title="Chart" className="min-w-0">
        <Empty>Select a stock from the list to see its chart and setup details.</Empty>
      </Card>
    );
  }
  return <StockView key={selection.symbol} selection={selection} onSelect={onSelect} onStockPage={onStockPage} />;
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-4 text-center first:pl-0 last:pr-0">
      <p className="text-[11px] text-mute">{label}</p>
      <p className="text-xl font-semibold tabular-nums text-ink">{value}</p>
    </div>
  );
}

function StockView({
  selection,
  onSelect,
  onStockPage,
}: {
  selection: Selection;
  onSelect: (s: Selection) => void;
  onStockPage: boolean;
}) {
  const bars = useBars(selection.symbol);
  const live = useLiveQuotes([selection.symbol]);
  const stock = useStockSetups(selection.symbol);
  const ranked = useSetups(selection.strategy);
  const [range, setRange] = useState<RangeId>("1Y");

  const setups = stock.data?.setups ?? [];
  const active = setups.find((s) => s.strategy_id === selection.strategy) ?? setups[0] ?? null;
  const rsRank = ranked.data?.rows.find((r) => r.symbol === selection.symbol)?.rs_rank ?? null;

  const list = bars.data?.bars ?? [];
  const last = list[list.length - 1];
  const prev = list[list.length - 2];
  const change =
    last?.close != null && prev?.close != null && prev.close !== 0
      ? (last.close / prev.close - 1) * 100
      : null;
  const money = last?.close != null && prev?.close != null ? last.close - prev.close : null;
  const company = bars.data?.company ?? stock.data?.company ?? null;

  return (
    <Card label={`Chart of ${selection.symbol}`} className="min-w-0">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-baseline gap-x-3">
            <span className="text-2xl font-semibold text-ink">{selection.symbol}</span>
            <WatchStar symbol={selection.symbol} />
            {company ? <span className="text-sm text-mute">{company}</span> : null}
            {onStockPage ? null : (
              <a href={`/stocks/${encodeURIComponent(selection.symbol)}`} className="text-xs text-accent hover:underline">
                Full analysis →
              </a>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-3">
            <span className="text-3xl font-semibold tabular-nums text-ink">{fmtPrice(last?.close)}</span>
            <span className={`text-sm tabular-nums ${tone(change)}`}>
              {money === null ? DASH : `${money > 0 ? "+" : ""}${fmtNum(money, 2)}`} ({fmtPct(change, 2, true)})
            </span>
          </div>
          <p className="mt-1 text-[11px] text-mute">
            {last ? `Close ${fmtDay(last.day)}, adjusted prices` : bars.isPending ? "" : "No price data"}
          </p>
          <LiveLine q={live.bySymbol.get(selection.symbol.toUpperCase())} />
        </div>
        <div className="flex flex-col items-end gap-3">
          {active ? (
            <span className="flex items-center gap-2 text-lg">
              <GradeBadge classification={active.classification} grade={active.grade} />
              <StatusPill status={active.status} />
            </span>
          ) : null}
          <div className="flex divide-x divide-line rounded-lg border border-line bg-panel2 px-4 py-2">
            <Figure label="Score" value={fmtNum(active?.score, 0)} />
            <Figure label="RS rank" value={fmtInt(rsRank)} />
            <Figure label="Pivot" value={fmtPrice(active?.pivot)} />
          </div>
        </div>
      </div>

      {setups.length > 1 ? (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs" role="group" aria-label="Setup shown on the chart">
          {setups.map((s) => (
            <button
              key={s.strategy_id}
              type="button"
              aria-pressed={active?.strategy_id === s.strategy_id}
              onClick={() => onSelect({ symbol: selection.symbol, strategy: s.strategy_id })}
              className={`rounded border px-2 py-1 ${
                active?.strategy_id === s.strategy_id
                  ? "border-accent bg-accent/10 text-ink"
                  : "border-line text-mute hover:text-ink"
              }`}
            >
              {strategyLabel(s.strategy_id)}
            </button>
          ))}
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
      {stock.isError ? <ErrorBox error={stock.error} /> : null}
    </Card>
  );
}
