"use client";

// The Chart page's chart (TradingView Lightweight Charts): daily, weekly or monthly bars as
// candles, hollow candles, Heikin Ashi, OHLC bars, a line or an area; moving averages and
// Bollinger bands on the price; volume, RSI and MACD in panes below it; the VCP setup's marks and
// its pivot and stop. All series are made once; settings only change their data and visibility.
import {
  LineStyle,
  PriceScaleMode,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesType,
  type Time,
} from "lightweight-charts";
import { type MutableRefObject, useEffect, useMemo, useRef, useState } from "react";
import { COLORS, buildMarkers, buildPriceLines } from "@/lib/chartData";
import { INDICATORS, type ChartSettings } from "@/lib/chartSettings";
import { fmtInt, fmtNum, fmtPct } from "@/lib/fmt";
import {
  aggregate,
  bollinger,
  dailyBars,
  ema,
  heikinAshi,
  lastPerBar,
  macd,
  periodOf,
  rangeStart,
  rsi,
  rsLabels,
  rsLine,
  rsNewHighs,
  sma,
  LABEL_GAP,
  YEAR_BARS,
  type OBar,
  type Series,
} from "@/lib/indicators";
import { useResolvedTheme } from "@/lib/prefs";
import type { Bar, StockSetup } from "@/lib/schemas";
import { chartTheme } from "./ChartView";

const RIGHT_GAP = 12; // empty bars after the last one: room for the "RS Rating NN" label
const AVERAGE_IDS = ["sma10", "sma20", "sma30", "sma50", "sma150", "sma200", "ema10", "ema21", "ema50", "ema200"];
const PANE_HEIGHT: Record<string, number> = { rs: 0.14, volume: 0.16, rsi: 0.14, macd: 0.16 };
const PANES = ["rs", "volume", "rsi", "macd"];
const color = (id: string) => INDICATORS.find((i) => i.id === id)?.color ?? COLORS.line;

type Line = ISeriesApi<"Line">;
type Handles = {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  bars: ISeriesApi<"Bar">;
  line: Line;
  area: ISeriesApi<"Area">;
  averages: Record<string, Line>;
  bbUpper: Line;
  bbMid: Line;
  bbLower: Line;
  volume: ISeriesApi<"Histogram">;
  volma: Line;
  rsi: Line;
  rs: Line;
  macdHist: ISeriesApi<"Histogram">;
  macdLine: Line;
  macdSignal: Line;
  priceLines: [ISeriesApi<SeriesType>, IPriceLine][];
};

/** What the chart draws, computed from the daily bars and the settings. */
export type Computed = {
  bars: OBar[]; // the interval's bars (real prices)
  drawn: OBar[]; // what the price series shows (Heikin Ashi when chosen)
  lines: Record<string, Series>;
  macd: { macd: Series; signal: Series; hist: Series } | null;
};

export type IndexClose = { day: string; close: number };

export type RsRank = { day: string; rs_rank: number };

export function compute(
  raw: Bar[],
  s: ChartSettings,
  benchmark: IndexClose[] | null = null,
  ranks: RsRank[] | null = null,
): Computed {
  const daily = dailyBars(raw);
  const bars = aggregate(daily, s.interval);
  const closes: Series = bars.map((b) => b.close);
  const on = new Set(s.indicators);
  const lines: Record<string, Series> = {};
  for (const id of AVERAGE_IDS) {
    if (!on.has(id)) continue;
    const n = Number(id.slice(3));
    lines[id] = id.startsWith("sma") ? sma(closes, n) : ema(closes, n);
  }
  if (on.has("bb")) {
    const b = bollinger(closes);
    lines.bbUpper = b.upper;
    lines.bbMid = b.mid;
    lines.bbLower = b.lower;
  }
  if (on.has("volma")) lines.volma = sma(bars.map((b) => b.volume), 50);
  if (on.has("rsi")) lines.rsi = rsi(closes);
  if (on.has("rs") && benchmark && benchmark.length > 0) lines.rs = rsLine(daily, benchmark, bars, s.interval);
  if (on.has("rs") && ranks && ranks.length > 0) {
    lines.rsRank = lastPerBar(ranks.map((r) => ({ day: r.day, value: r.rs_rank })), daily, bars, s.interval);
  }
  return {
    bars,
    drawn: s.type === "heikin" ? heikinAshi(bars) : bars,
    lines,
    macd: on.has("macd") ? macd(closes) : null,
  };
}

const points = (bars: OBar[], values: Series | undefined) =>
  values
    ? bars.flatMap((b, i) => {
        const v = values[i];
        return v === null || v === undefined ? [] : [{ time: b.day as Time, value: v }];
      })
    : [];

type Props = {
  symbol: string;
  bars: Bar[];
  setup: StockSetup | null;
  settings: ChartSettings;
  /** the RS line's benchmark closes (null while loading or when the RS line is off) */
  benchmark?: IndexClose[] | null;
  /** the stock's RS rank per scan day, for the RS Rating labels */
  rsRanks?: RsRank[] | null;
  /** the chart itself, for the page's screenshot button */
  apiRef?: MutableRefObject<IChartApi | null>;
};

export function ProChart({ symbol, bars, setup, settings, benchmark = null, rsRanks = null, apiRef }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const h = useRef<Handles | null>(null);
  const theme = useResolvedTheme();
  const [hover, setHover] = useState<number | null>(null);
  const data = useMemo(() => compute(bars, settings, benchmark, rsRanks), [bars, settings, benchmark, rsRanks]);
  const on = useMemo(() => new Set(settings.indicators), [settings.indicators]);

  // the chart and every series, once
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, { ...chartTheme(), width: el.clientWidth, height: el.clientHeight || 520 });
    const quiet = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
    const line = (c: string, opts: object = {}) => chart.addLineSeries({ color: c, lineWidth: 1, ...quiet, ...opts });
    const averages: Record<string, Line> = {};
    for (const id of AVERAGE_IDS) averages[id] = line(color(id), { visible: false });
    const bb = color("bb");
    h.current = {
      chart,
      candles: chart.addCandlestickSeries({
        upColor: COLORS.up, downColor: COLORS.down, borderVisible: false,
        wickUpColor: COLORS.up, wickDownColor: COLORS.down,
      }),
      bars: chart.addBarSeries({ upColor: COLORS.up, downColor: COLORS.down, visible: false }),
      line: chart.addLineSeries({ color: COLORS.line, lineWidth: 2, visible: false, priceLineVisible: false }),
      area: chart.addAreaSeries({
        lineColor: COLORS.line, topColor: "rgba(138,180,255,0.35)", bottomColor: "rgba(138,180,255,0.02)",
        lineWidth: 2, visible: false, priceLineVisible: false,
      }),
      averages,
      bbUpper: line(bb, { visible: false, lineStyle: LineStyle.Dashed }),
      bbMid: line(bb, { visible: false }),
      bbLower: line(bb, { visible: false, lineStyle: LineStyle.Dashed }),
      volume: chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", ...quiet }),
      volma: line(color("volma"), { priceScaleId: "vol", visible: false }),
      rsi: line(color("rsi"), { priceScaleId: "rsi", visible: false }),
      rs: line(color("rs"), { priceScaleId: "rs", visible: false, lineWidth: 2 }),
      macdHist: chart.addHistogramSeries({ priceScaleId: "macd", visible: false, ...quiet }),
      macdLine: line(color("macd"), { priceScaleId: "macd", visible: false }),
      macdSignal: line("#ff9f1c", { priceScaleId: "macd", visible: false }),
      priceLines: [],
    };
    for (const lvl of [70, 30]) {
      h.current.rsi.createPriceLine({
        price: lvl, color: "rgba(160,160,160,0.6)", lineWidth: 1, lineStyle: LineStyle.Dashed,
        axisLabelVisible: false, title: "",
      });
    }
    if (apiRef) apiRef.current = chart;
    const move = (p: { logical?: number }) => setHover(p.logical === undefined ? null : Math.round(p.logical));
    chart.subscribeCrosshairMove(move);
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth, height: el.clientHeight || 520 }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.unsubscribeCrosshairMove(move);
      chart.remove();
      h.current = null;
      if (apiRef) apiRef.current = null;
    };
  }, [apiRef]);

  useEffect(() => {
    h.current?.chart.applyOptions(chartTheme());
  }, [theme]);

  // data, visibility, panes, marks and the visible range
  useEffect(() => {
    const c = h.current;
    if (!c) return;
    const { bars: ob, drawn, lines } = data;
    const type = settings.type;
    const ohlc = drawn.map((b) => ({ time: b.day as Time, open: b.open, high: b.high, low: b.low, close: b.close }));
    const closes = drawn.map((b) => ({ time: b.day as Time, value: b.close }));
    const candleLike = type === "candles" || type === "hollow" || type === "heikin";
    c.candles.setData(candleLike ? ohlc : []);
    c.candles.applyOptions(
      type === "hollow"
        ? { visible: true, upColor: "rgba(0,0,0,0)", borderVisible: true, borderUpColor: COLORS.up, borderDownColor: COLORS.down }
        : { visible: candleLike, upColor: COLORS.up, borderVisible: false },
    );
    c.bars.setData(type === "ohlc" ? ohlc : []);
    c.bars.applyOptions({ visible: type === "ohlc" });
    c.line.setData(type === "line" ? closes : []);
    c.line.applyOptions({ visible: type === "line" });
    c.area.setData(type === "area" ? closes : []);
    c.area.applyOptions({ visible: type === "area" });
    const main: ISeriesApi<SeriesType> =
      type === "ohlc" ? c.bars : type === "line" ? c.line : type === "area" ? c.area : c.candles;

    for (const id of AVERAGE_IDS) {
      c.averages[id]!.setData(points(ob, lines[id]));
      c.averages[id]!.applyOptions({ visible: on.has(id) });
    }
    c.bbUpper.setData(points(ob, lines.bbUpper));
    c.bbMid.setData(points(ob, lines.bbMid));
    c.bbLower.setData(points(ob, lines.bbLower));
    for (const s of [c.bbUpper, c.bbMid, c.bbLower]) s.applyOptions({ visible: on.has("bb") });

    // panes under the price, top to bottom: volume, RSI, MACD (one chart, stacked price scales)
    const panes = PANES.filter((p) => on.has(p));
    const lower = panes.reduce((a, p) => a + PANE_HEIGHT[p]!, 0);
    c.chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.05, bottom: lower + (panes.length ? 0.03 : 0.05) },
      mode: settings.log ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    });
    let top = 1 - lower;
    for (const p of panes) {
      const ht = PANE_HEIGHT[p]!;
      c.chart.priceScale(p === "volume" ? "vol" : p).applyOptions({
        scaleMargins: { top: top + 0.015, bottom: Math.max(0, 1 - top - ht) },
      });
      top += ht;
    }
    const volOn = on.has("volume");
    c.volume.setData(
      volOn
        ? ob.flatMap((b) => (b.volume === null ? [] : [{
            time: b.day as Time, value: b.volume, color: b.close >= b.open ? COLORS.upFade : COLORS.downFade,
          }]))
        : [],
    );
    c.volume.applyOptions({ visible: volOn });
    c.volma.setData(volOn ? points(ob, lines.volma) : []);
    c.volma.applyOptions({ visible: volOn && on.has("volma") });
    c.rsi.setData(points(ob, lines.rsi));
    c.rsi.applyOptions({ visible: on.has("rsi") });
    c.rs.setData(points(ob, lines.rs));
    c.rs.applyOptions({ visible: on.has("rs") });
    // a dot where the RS line makes a new one-year high; the first dot of each run and the
    // line's last bar carry the RS rank of that day ("RS 91", "RS Rating 87")
    const rsHighs = lines.rs ? rsNewHighs(lines.rs, YEAR_BARS[settings.interval]) : [];
    let lastRs = -1;
    if (lines.rs) for (let k = lines.rs.length - 1; k >= 0; k--) if (lines.rs[k] !== null) { lastRs = k; break; }
    const labels = lines.rs ? rsLabels(rsHighs, lines.rsRank ?? [], lastRs, LABEL_GAP[settings.interval]) : [];
    c.rs.setMarkers(
      labels.map((l) => ({
        time: ob[l.index]!.day as Time,
        position: (l.text ? "aboveBar" : "inBar") as "aboveBar" | "inBar",
        shape: "circle" as const,
        color: color("rs"),
        size: l.dot ? 0.5 : 0.1,
        text: l.text,
      })),
    );
    const m = data.macd;
    c.macdLine.setData(points(ob, m?.macd));
    c.macdSignal.setData(points(ob, m?.signal));
    c.macdHist.setData(
      m
        ? ob.flatMap((b, i) => {
            const v = m.hist[i];
            return v === null || v === undefined ? [] : [{ time: b.day as Time, value: v, color: v >= 0 ? COLORS.upFade : COLORS.downFade }];
          })
        : [],
    );
    for (const s of [c.macdLine, c.macdSignal, c.macdHist]) s.applyOptions({ visible: m !== null });

    // the VCP setup's marks, each moved to the bar (week or month) its day falls in
    const period = periodOf(dailyBars(bars), settings.interval);
    const marks = on.has("vcp")
      ? buildMarkers(bars, setup).flatMap((mk) => {
          const t = period.get(mk.time);
          return t ? [{ ...mk, time: t as Time }] : [];
        })
      : [];
    for (const s of [c.candles, c.bars, c.line, c.area] as ISeriesApi<SeriesType>[]) s.setMarkers(s === main ? marks : []);
    for (const [s, l] of c.priceLines) s.removePriceLine(l);
    c.priceLines = on.has("levels")
      ? buildPriceLines(setup).map((l): [ISeriesApi<SeriesType>, IPriceLine] => [
          main,
          main.createPriceLine({
            price: l.price, color: l.color, lineWidth: 1, axisLabelVisible: true, title: l.title,
            lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
          }),
        ])
      : [];

  }, [data, settings, on, bars, setup, theme]);

  // the visible range: only when the stock, the interval or the range changes, so turning an
  // indicator on or off keeps the user's own zoom
  const ob = data.bars;
  const lastDay = ob[ob.length - 1]?.day ?? null;
  const rangeKey = `${symbol}|${settings.interval}|${settings.range}|${ob.length}|${lastDay}`;
  useEffect(() => {
    const c = h.current;
    if (!c) return;
    if (!lastDay) {
      c.chart.timeScale().fitContent();
      return;
    }
    const from = rangeStart(lastDay, settings.range);
    const start = from ? Math.max(0, ob.findIndex((b) => b.day >= from)) : 0;
    c.chart.timeScale().setVisibleLogicalRange({ from: start, to: ob.length - 1 + RIGHT_GAP });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeKey]);

  const i = hover !== null && hover >= 0 && hover < ob.length ? hover : ob.length - 1;
  const bar = ob[i];
  const prev = ob[i - 1];
  const change = bar && prev ? (bar.close / prev.close - 1) * 100 : null;
  const val = (id: string) => data.lines[id]?.[i] ?? null;
  const legendLines = [
    ...AVERAGE_IDS.filter((id) => on.has(id)).map((id) => ({ id, label: INDICATORS.find((x) => x.id === id)!.label, v: val(id), digits: 2 })),
    ...(on.has("bb") ? [{ id: "bb", label: "BB", v: val("bbMid"), digits: 2 }] : []),
    ...(on.has("rs") ? [{ id: "rs", label: `RS vs ${settings.benchmark}`, v: val("rs"), digits: 1 }] : []),
    ...(on.has("rs") && val("rsRank") !== null ? [{ id: "rs", label: "RS Rating", v: val("rsRank"), digits: 0 }] : []),
    ...(on.has("rsi") ? [{ id: "rsi", label: "RSI 14", v: val("rsi"), digits: 1 }] : []),
    ...(on.has("macd") ? [{ id: "macd", label: "MACD", v: data.macd?.macd[i] ?? null, digits: 2 }] : []),
  ];
  const INTERVAL = { D: "1D", W: "1W", M: "1M" } as const;

  return (
    <div className="relative h-full min-h-[480px] w-full">
      <div ref={box} data-testid="pro-chart" className="absolute inset-0" />
      <div data-testid="chart-legend" className="pointer-events-none absolute left-2 top-1 z-10 max-w-[calc(100%-5rem)] space-y-0.5 rounded bg-panel/75 px-2 py-1 text-xs tabular-nums">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold text-ink">{symbol} · {INTERVAL[settings.interval]} · NSE</span>
          {bar ? (
            <>
              <span className="text-mute">{bar.day}</span>
              <span className="text-mute">O <span className="text-ink">{fmtNum(bar.open, 2)}</span></span>
              <span className="text-mute">H <span className="text-ink">{fmtNum(bar.high, 2)}</span></span>
              <span className="text-mute">L <span className="text-ink">{fmtNum(bar.low, 2)}</span></span>
              <span className="text-mute">C <span className="text-ink">{fmtNum(bar.close, 2)}</span></span>
              <span className={change === null ? "text-mute" : change >= 0 ? "text-up" : "text-down"}>{fmtPct(change, 2, true)}</span>
              {on.has("volume") ? <span className="text-mute">Vol <span className="text-ink">{fmtInt(bar.volume)}</span></span> : null}
            </>
          ) : null}
        </p>
        {legendLines.length > 0 ? (
          <p className="flex flex-wrap gap-x-3">
            {legendLines.map((l) => (
              <span key={l.label} style={{ color: color(l.id) }}>
                {l.label} {fmtNum(l.v, l.digits)}
              </span>
            ))}
          </p>
        ) : null}
      </div>
    </div>
  );
}
