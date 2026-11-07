"use client";

// The Chart page's chart (TradingView Lightweight Charts): daily, weekly or monthly bars as
// candles, hollow candles, Heikin Ashi, OHLC bars, a line or an area; any number of moving
// averages and Bollinger bands on the price; the RS line, volume (and its average), RSI and MACD
// in panes below it; the VCP setup's marks and its pivot and stop. Every period and colour comes
// from the settings. The fixed series are made once; the averages are added and removed as the
// settings change.
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
import { buildMarkers, buildPriceLines } from "@/lib/chartData";
import { fade, maLabel, type ChartSettings, type Colors, type Params } from "@/lib/chartSettings";
import { fmtInt, fmtNum, fmtPct } from "@/lib/fmt";
import {
  aggregate,
  bollinger,
  dailyBars,
  ema,
  heikinAshi,
  lastPerBar,
  LABEL_GAP,
  macd,
  periodOf,
  rangeStart,
  rsi,
  rsLabels,
  rsLine,
  rsNewHighs,
  sma,
  type Interval,
  type OBar,
  type Series,
} from "@/lib/indicators";
import { useResolvedTheme } from "@/lib/prefs";
import type { Bar, StockSetup } from "@/lib/schemas";
import { chartTheme } from "./ChartView";

const RIGHT_GAP = 12; // empty bars after the last one: room for the "RS Rating NN" label
const PANE_HEIGHT: Record<string, number> = { rs: 0.14, volume: 0.16, rsi: 0.14, macd: 0.16 };
const PANES = ["rs", "volume", "rsi", "macd"] as const;
const VOLUME_ALPHA = 0.45;

/** The RS line's new-high lookback in bars, from the setting in weeks. */
export const rsLookback = (weeks: number, interval: Interval) =>
  interval === "D" ? weeks * 5 : interval === "W" ? weeks : Math.max(2, Math.round(weeks / 4.345));

type Line = ISeriesApi<"Line">;
type Handles = {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  bars: ISeriesApi<"Bar">;
  line: Line;
  area: ISeriesApi<"Area">;
  averages: Map<string, Line>;
  bbUpper: Line;
  bbMid: Line;
  bbLower: Line;
  volume: ISeriesApi<"Histogram">;
  volma: Line;
  rsi: Line;
  rsiLevels: IPriceLine[];
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
  const on = new Set<string>(s.toggles);
  const p = s.params;
  const lines: Record<string, Series> = {};
  for (const a of s.averages) {
    if (a.on) lines[a.id] = a.kind === "SMA" ? sma(closes, a.period) : ema(closes, a.period);
  }
  if (on.has("bb")) {
    const b = bollinger(closes, p.bb.period, p.bb.mult);
    lines.bbUpper = b.upper;
    lines.bbMid = b.mid;
    lines.bbLower = b.lower;
  }
  if (on.has("volma")) {
    const vols = bars.map((b) => b.volume);
    lines.volma = p.volMa.kind === "SMA" ? sma(vols, p.volMa.period) : ema(vols, p.volMa.period);
  }
  if (on.has("rsi")) lines.rsi = rsi(closes, p.rsi.period);
  if (on.has("rs") && benchmark && benchmark.length > 0) lines.rs = rsLine(daily, benchmark, bars, s.interval);
  if (on.has("rs") && ranks && ranks.length > 0) {
    lines.rsRank = lastPerBar(ranks.map((r) => ({ day: r.day, value: r.rs_rank })), daily, bars, s.interval);
  }
  return {
    bars,
    drawn: s.type === "heikin" ? heikinAshi(bars) : bars,
    lines,
    macd: on.has("macd") ? macd(closes, p.macd.fast, p.macd.slow, p.macd.signal) : null,
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
  const on = useMemo(() => new Set<string>(settings.toggles), [settings.toggles]);
  const col: Colors = settings.colors;

  // the chart and its fixed series, once (colours and data come with the settings below)
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, { ...chartTheme(), width: el.clientWidth, height: el.clientHeight || 520 });
    const quiet = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
    const line = (opts: object = {}) => chart.addLineSeries({ lineWidth: 1, visible: false, ...quiet, ...opts });
    h.current = {
      chart,
      candles: chart.addCandlestickSeries({ borderVisible: false }),
      bars: chart.addBarSeries({ visible: false }),
      line: chart.addLineSeries({ lineWidth: 2, visible: false, priceLineVisible: false }),
      area: chart.addAreaSeries({ lineWidth: 2, visible: false, priceLineVisible: false }),
      averages: new Map(),
      bbUpper: line({ lineStyle: LineStyle.Dashed }),
      bbMid: line(),
      bbLower: line({ lineStyle: LineStyle.Dashed }),
      volume: chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", ...quiet }),
      volma: line({ priceScaleId: "vol" }),
      rsi: line({ priceScaleId: "rsi" }),
      rsiLevels: [],
      rs: line({ priceScaleId: "rs", lineWidth: 2 }),
      macdHist: chart.addHistogramSeries({ priceScaleId: "macd", visible: false, ...quiet }),
      macdLine: line({ priceScaleId: "macd" }),
      macdSignal: line({ priceScaleId: "macd" }),
      priceLines: [],
    };
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

  // data, colours, visibility, panes and marks
  useEffect(() => {
    const c = h.current;
    if (!c) return;
    const { bars: ob, drawn, lines } = data;
    const p: Params = settings.params;
    const type = settings.type;
    const ohlc = drawn.map((b) => ({ time: b.day as Time, open: b.open, high: b.high, low: b.low, close: b.close }));
    const closes = drawn.map((b) => ({ time: b.day as Time, value: b.close }));
    const candleLike = type === "candles" || type === "hollow" || type === "heikin";
    c.candles.setData(candleLike ? ohlc : []);
    c.candles.applyOptions({
      visible: candleLike,
      upColor: type === "hollow" ? "rgba(0,0,0,0)" : col.up,
      downColor: col.down,
      borderVisible: type === "hollow",
      borderUpColor: col.up,
      borderDownColor: col.down,
      wickUpColor: col.wickUp,
      wickDownColor: col.wickDown,
    });
    c.bars.setData(type === "ohlc" ? ohlc : []);
    c.bars.applyOptions({ visible: type === "ohlc", upColor: col.up, downColor: col.down });
    c.line.setData(type === "line" ? closes : []);
    c.line.applyOptions({ visible: type === "line", color: col.line });
    c.area.setData(type === "area" ? closes : []);
    c.area.applyOptions({
      visible: type === "area",
      lineColor: col.line,
      topColor: fade(col.line, 0.35),
      bottomColor: fade(col.line, 0.02),
    });
    const main: ISeriesApi<SeriesType> =
      type === "ohlc" ? c.bars : type === "line" ? c.line : type === "area" ? c.area : c.candles;

    // moving averages: one series per average in the settings
    const keep = new Set(settings.averages.map((a) => a.id));
    for (const [id, s] of c.averages) {
      if (!keep.has(id)) {
        c.chart.removeSeries(s);
        c.averages.delete(id);
      }
    }
    for (const a of settings.averages) {
      let s = c.averages.get(a.id);
      if (!s) {
        s = c.chart.addLineSeries({ lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        c.averages.set(a.id, s);
      }
      s.setData(points(ob, lines[a.id]));
      s.applyOptions({ visible: a.on, color: a.color });
    }
    c.bbUpper.setData(points(ob, lines.bbUpper));
    c.bbMid.setData(points(ob, lines.bbMid));
    c.bbLower.setData(points(ob, lines.bbLower));
    for (const s of [c.bbUpper, c.bbMid, c.bbLower]) s.applyOptions({ visible: on.has("bb"), color: col.bb });

    // panes under the price, top to bottom: RS line, volume, RSI, MACD (stacked price scales)
    const panes = PANES.filter((x) => on.has(x));
    const lower = panes.reduce((acc, x) => acc + PANE_HEIGHT[x]!, 0);
    c.chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.05, bottom: lower + (panes.length ? 0.03 : 0.05) },
      mode: settings.log ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    });
    let top = 1 - lower;
    for (const x of panes) {
      const ht = PANE_HEIGHT[x]!;
      c.chart.priceScale(x === "volume" ? "vol" : x).applyOptions({
        scaleMargins: { top: top + 0.015, bottom: Math.max(0, 1 - top - ht) },
      });
      top += ht;
    }
    const volOn = on.has("volume");
    const vUp = fade(col.volUp, VOLUME_ALPHA);
    const vDown = fade(col.volDown, VOLUME_ALPHA);
    c.volume.setData(
      volOn
        ? ob.flatMap((b) => (b.volume === null ? [] : [{ time: b.day as Time, value: b.volume, color: b.close >= b.open ? vUp : vDown }]))
        : [],
    );
    c.volume.applyOptions({ visible: volOn });
    c.volma.setData(volOn ? points(ob, lines.volma) : []);
    c.volma.applyOptions({ visible: volOn && on.has("volma"), color: col.volma });
    c.rsi.setData(points(ob, lines.rsi));
    c.rsi.applyOptions({ visible: on.has("rsi"), color: col.rsi });
    for (const l of c.rsiLevels) c.rsi.removePriceLine(l);
    c.rsiLevels = [p.rsi.upper, p.rsi.lower].map((lvl) =>
      c.rsi.createPriceLine({ price: lvl, color: "rgba(160,160,160,0.6)", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" }),
    );
    c.rs.setData(points(ob, lines.rs));
    c.rs.applyOptions({ visible: on.has("rs"), color: col.rs });
    const m = data.macd;
    c.macdLine.setData(points(ob, m?.macd));
    c.macdSignal.setData(points(ob, m?.signal));
    c.macdHist.setData(
      m
        ? ob.flatMap((b, i) => {
            const v = m.hist[i];
            return v === null || v === undefined ? [] : [{ time: b.day as Time, value: v, color: v >= 0 ? vUp : vDown }];
          })
        : [],
    );
    c.macdLine.applyOptions({ visible: m !== null, color: col.macd });
    c.macdSignal.applyOptions({ visible: m !== null, color: col.macdSignal });
    c.macdHist.applyOptions({ visible: m !== null });

    // a dot where the RS line makes a new high over the lookback; the first dot of each run and
    // the line's last bar carry the RS rank of that day ("RS 91", "RS Rating 87")
    const rsHighs = lines.rs ? rsNewHighs(lines.rs, rsLookback(p.rs.weeks, settings.interval)) : [];
    let lastRs = -1;
    if (lines.rs) for (let k = lines.rs.length - 1; k >= 0; k--) if (lines.rs[k] !== null) { lastRs = k; break; }
    const labels = lines.rs ? rsLabels(rsHighs, lines.rsRank ?? [], lastRs, LABEL_GAP[settings.interval]) : [];
    c.rs.setMarkers(
      labels.map((l) => ({
        time: ob[l.index]!.day as Time,
        position: (l.text ? "aboveBar" : "inBar") as "aboveBar" | "inBar",
        shape: "circle" as const,
        color: col.rs,
        size: l.dot ? 0.5 : 0.1,
        text: l.text,
      })),
    );

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
            price: l.price,
            color: l.title === "Stop" ? col.stop : col.pivot,
            lineWidth: 1,
            axisLabelVisible: true,
            title: l.title,
            lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
          }),
        ])
      : [];
  }, [data, settings, on, col, bars, setup, theme]);

  // the visible range: only when the stock, the interval or the range changes, so changing an
  // indicator keeps the user's own zoom
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
  const p = settings.params;
  const legendLines: { key: string; label: string; v: number | null; digits: number; color: string }[] = [
    ...settings.averages
      .filter((a) => a.on)
      .map((a) => ({ key: a.id, label: maLabel(a), v: val(a.id), digits: 2, color: a.color })),
    ...(on.has("bb") ? [{ key: "bb", label: `BB ${p.bb.period}, ${p.bb.mult}`, v: val("bbMid"), digits: 2, color: col.bb }] : []),
    ...(on.has("rs") ? [{ key: "rs", label: `RS vs ${settings.benchmark}`, v: val("rs"), digits: 1, color: col.rs }] : []),
    ...(on.has("rs") && val("rsRank") !== null ? [{ key: "rsRank", label: "RS Rating", v: val("rsRank"), digits: 0, color: col.rs }] : []),
    ...(on.has("volume") && on.has("volma")
      ? [{ key: "volma", label: `Vol ${maLabel(p.volMa)}`, v: val("volma"), digits: 0, color: col.volma }]
      : []),
    ...(on.has("rsi") ? [{ key: "rsi", label: `RSI ${p.rsi.period}`, v: val("rsi"), digits: 1, color: col.rsi }] : []),
    ...(on.has("macd")
      ? [{ key: "macd", label: `MACD ${p.macd.fast}, ${p.macd.slow}, ${p.macd.signal}`, v: data.macd?.macd[i] ?? null, digits: 2, color: col.macd }]
      : []),
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
              <span style={{ color: change === null ? undefined : change >= 0 ? col.up : col.down }} className={change === null ? "text-mute" : ""}>
                {fmtPct(change, 2, true)}
              </span>
              {on.has("volume") ? <span className="text-mute">Vol <span className="text-ink">{fmtInt(bar.volume)}</span></span> : null}
            </>
          ) : null}
        </p>
        {legendLines.length > 0 ? (
          <p className="flex flex-wrap gap-x-3">
            {legendLines.map((l) => (
              <span key={l.key} style={{ color: l.color }}>
                {l.label} {fmtNum(l.v, l.digits)}
              </span>
            ))}
          </p>
        ) : null}
      </div>
    </div>
  );
}
