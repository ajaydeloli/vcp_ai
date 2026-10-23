"use client";

// One past run: daily candles from 40 sessions before the launch to 60 after the top, the 50-day
// average, the launch and top marks, and each base drawn as a flat line at its high up to its
// breakout (B1, B2 ...). Read only.
import { createChart, LineStyle, type IChartApi } from "lightweight-charts";
import { useEffect, useRef } from "react";
import { COLORS, toCandles, toSma } from "@/lib/chartData";
import { baseSegments, leaderMarkers } from "@/lib/leaders";
import { useResolvedTheme } from "@/lib/prefs";
import type { LeaderRun } from "@/lib/schemas";
import { chartTheme } from "./ChartView";

export function LeaderChart({ data }: { data: LeaderRun }) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const theme = useResolvedTheme();

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, { ...chartTheme(), width: el.clientWidth, height: el.clientHeight || 360 });
    chartRef.current = chart;
    const candles = chart.addCandlestickSeries({
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderVisible: false,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });
    candles.setData(toCandles(data.bars));
    candles.setMarkers(leaderMarkers(data.run, data.bases, data.bars));
    const sma50 = chart.addLineSeries({ color: COLORS.sma50, lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
    sma50.setData(toSma(data.bars, "sma50"));
    for (const seg of baseSegments(data.bases, data.bars)) {
      const line = chart.addLineSeries({
        color: COLORS.breakout,
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        title: "",
      });
      line.setData(seg.points);
    }
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth, height: el.clientHeight || 360 }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  useEffect(() => {
    chartRef.current?.applyOptions(chartTheme());
  }, [theme]);

  return <div ref={box} className="h-[360px] w-full" role="img" aria-label={`${data.run.symbol} price chart with the launch, the bases and the top marked`} />;
}
