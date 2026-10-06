"use client";

// Candlestick chart (TradingView Lightweight Charts): candles, volume, 20/50/200-day averages,
// and the selected strategy's marks (base start, contractions / points, breakout, pivot and stop).
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import {
  COLORS,
  RANGES,
  buildMarkers,
  buildPriceLines,
  rangeFrom,
  toCandles,
  toSma,
  toVolume,
  type RangeId,
} from "@/lib/chartData";
import type { Bar, StockSetup } from "@/lib/schemas";

type Props = {
  bars: Bar[];
  setup: StockSetup | null;
  range: RangeId;
  onRange: (r: RangeId) => void;
};

export const CHART_THEME = {
  layout: {
    background: { type: ColorType.Solid, color: "#0d1422" },
    textColor: "#8a97b3",
  },
  grid: { vertLines: { color: "#142036" }, horzLines: { color: "#142036" } },
  rightPriceScale: { borderColor: "#1b2740" },
  timeScale: { borderColor: "#1b2740", timeVisible: false },
  crosshair: { mode: CrosshairMode.Normal },
} as const;

type Handles = {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
  sma20: ISeriesApi<"Line">;
  sma50: ISeriesApi<"Line">;
  sma200: ISeriesApi<"Line">;
  lines: IPriceLine[];
};

export function ChartView({ bars, setup, range, onRange }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const h = useRef<Handles | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, { ...CHART_THEME, width: el.clientWidth, height: 340 });
    const candles = chart.addCandlestickSeries({
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderVisible: false,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });
    const volume = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const line = (color: string) =>
      chart.addLineSeries({
        color,
        lineWidth: 1,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
    h.current = {
      chart,
      candles,
      volume,
      sma20: line(COLORS.sma20),
      sma50: line(COLORS.sma50),
      sma200: line(COLORS.sma200),
      lines: [],
    };
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      h.current = null;
    };
  }, []);

  useEffect(() => {
    const c = h.current;
    if (!c) return;
    c.candles.setData(toCandles(bars));
    c.volume.setData(toVolume(bars));
    c.sma20.setData(toSma(bars, "sma20"));
    c.sma50.setData(toSma(bars, "sma50"));
    c.sma200.setData(toSma(bars, "sma200"));
    c.candles.setMarkers(buildMarkers(bars, setup));
    for (const l of c.lines) c.candles.removePriceLine(l);
    c.lines = buildPriceLines(setup).map((l) =>
      c.candles.createPriceLine({
        price: l.price,
        color: l.color,
        lineWidth: 1,
        lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
        axisLabelVisible: true,
        title: l.title,
      }),
    );
    const from = rangeFrom(bars, range);
    const last = bars[bars.length - 1]?.day;
    if (from && last) {
      c.chart.timeScale().setVisibleRange({ from: from as Time, to: last as Time });
    } else {
      c.chart.timeScale().fitContent();
    }
  }, [bars, setup, range]);

  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs">
        <div className="flex gap-1" role="group" aria-label="Chart range">
          {RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              aria-pressed={range === r.id}
              onClick={() => onRange(r.id)}
              className={`rounded px-2 py-1 ${
                range === r.id ? "bg-accent text-white" : "bg-panel2 text-mute hover:text-ink"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
        <div className="flex gap-3 text-mute">
          <span style={{ color: COLORS.sma20 }}>SMA 20</span>
          <span style={{ color: COLORS.sma50 }}>SMA 50</span>
          <span style={{ color: COLORS.sma200 }}>SMA 200</span>
          <span>Adjusted prices</span>
        </div>
      </div>
      <div ref={box} data-testid="price-chart" className="w-full" />
    </div>
  );
}
