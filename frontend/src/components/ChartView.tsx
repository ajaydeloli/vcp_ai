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
import { useEffect, useRef, useState } from "react";
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
  ohlc: ISeriesApi<"Bar">;
  volume: ISeriesApi<"Histogram">;
  sma20: ISeriesApi<"Line">;
  sma50: ISeriesApi<"Line">;
  sma200: ISeriesApi<"Line">;
  lines: [ISeriesApi<"Candlestick" | "Bar">, IPriceLine][];
};

type Averages = { sma20: boolean; sma50: boolean; sma200: boolean };
const AVERAGES: { key: keyof Averages; label: string; color: string }[] = [
  { key: "sma20", label: "SMA 20", color: COLORS.sma20 },
  { key: "sma50", label: "SMA 50", color: COLORS.sma50 },
  { key: "sma200", label: "SMA 200", color: COLORS.sma200 },
];
type ChartType = "candles" | "ohlc";

export function ChartView({ bars, setup, range, onRange }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const h = useRef<Handles | null>(null);
  const [type, setType] = useState<ChartType>("candles");
  const [shown, setShown] = useState<Averages>({ sma20: true, sma50: true, sma200: true });

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, { ...CHART_THEME, width: el.clientWidth, height: el.clientHeight || 340 });
    const candles = chart.addCandlestickSeries({
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderVisible: false,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });
    const ohlc = chart.addBarSeries({ upColor: COLORS.up, downColor: COLORS.down, visible: false });
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
      ohlc,
      volume,
      sma20: line(COLORS.sma20),
      sma50: line(COLORS.sma50),
      sma200: line(COLORS.sma200),
      lines: [],
    };
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth, height: el.clientHeight || 340 }));
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
    c.ohlc.setData(toCandles(bars));
    c.volume.setData(toVolume(bars));
    c.sma20.setData(toSma(bars, "sma20"));
    c.sma50.setData(toSma(bars, "sma50"));
    c.sma200.setData(toSma(bars, "sma200"));
    const markers = buildMarkers(bars, setup);
    c.candles.setMarkers(markers);
    c.ohlc.setMarkers(markers);
    for (const [s, l] of c.lines) s.removePriceLine(l);
    const specs = buildPriceLines(setup);
    c.lines = [c.candles, c.ohlc].flatMap((s) =>
      specs.map((l): [typeof s, IPriceLine] => [
        s,
        s.createPriceLine({
          price: l.price,
          color: l.color,
          lineWidth: 1,
          lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
          axisLabelVisible: true,
          title: l.title,
        }),
      ]),
    );
    const from = rangeFrom(bars, range);
    const last = bars[bars.length - 1]?.day;
    if (from && last) {
      c.chart.timeScale().setVisibleRange({ from: from as Time, to: last as Time });
    } else {
      c.chart.timeScale().fitContent();
    }
  }, [bars, setup, range]);

  // which series is drawn (candles or OHLC bars) and which averages are shown
  useEffect(() => {
    const c = h.current;
    if (!c) return;
    c.candles.applyOptions({ visible: type === "candles" });
    c.ohlc.applyOptions({ visible: type === "ohlc" });
    c.sma20.applyOptions({ visible: shown.sma20 });
    c.sma50.applyOptions({ visible: shown.sma50 });
    c.sma200.applyOptions({ visible: shown.sma200 });
  }, [type, shown]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
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
        <div className="flex flex-wrap items-center gap-3 text-mute">
          <div className="flex gap-1" role="group" aria-label="Chart type">
            {(["candles", "ohlc"] as const).map((k) => (
              <button
                key={k}
                type="button"
                aria-pressed={type === k}
                onClick={() => setType(k)}
                className={`rounded px-2 py-1 ${type === k ? "bg-accent text-white" : "bg-panel2 text-mute hover:text-ink"}`}
              >
                {k === "candles" ? "Candles" : "OHLC"}
              </button>
            ))}
          </div>
          {AVERAGES.map((a) => (
            <label key={a.key} className="flex cursor-pointer items-center gap-1" style={{ color: a.color }}>
              <input
                type="checkbox"
                checked={shown[a.key]}
                onChange={(e) => setShown((s) => ({ ...s, [a.key]: e.target.checked }))}
              />
              {a.label}
            </label>
          ))}
          <span>Adjusted prices</span>
        </div>
      </div>
      <div className="relative min-h-[380px] flex-1">
        <div ref={box} data-testid="price-chart" className="absolute inset-0" />
      </div>
    </div>
  );
}
