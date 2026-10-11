"use client";

// A small line chart for the market overview (index vs its 50-day average; breadth with the
// regime threshold).
import { createChart, CrosshairMode, LineStyle, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";
import { CHART_THEME } from "./ChartView";

export type MiniSeries = {
  name: string;
  color: string;
  /** `color` per point: only for "dots" (each dot its own colour) */
  data: { time: string; value: number; color?: string }[];
  /** "bar": a histogram from zero (daily counts); "dots": small round dots on one line in a band
   *  at the bottom of the chart, just above the dates, on a scale of their own (value 0), so they
   *  never cover a line; default a line */
  kind?: "line" | "bar" | "dots";
  /** small marks on this series, e.g. distribution days on the index line */
  markers?: { time: string; color: string; text?: string; position?: "aboveBar" | "belowBar" }[];
};

type Props = {
  series: MiniSeries[];
  height?: number;
  /** dashed horizontal lines, e.g. the breadth threshold of the regime */
  threshold?: { value: number; title: string } | { value: number; title: string }[];
  label: string;
  /** the legend sits below the chart: no names on the lines, each line's value on the axis in its colour */
  plain?: boolean;
  /** whole numbers on the axis (counts), no decimals */
  whole?: boolean;
};

export function MiniLineChart({ series, height = 120, threshold, label, plain, whole }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const linesRef = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, {
      ...CHART_THEME,
      width: el.clientWidth,
      height,
      handleScroll: false,
      handleScale: false,
      // the cross snaps to the nearest point of a line, so the exact value can be read
      crosshair: { mode: CrosshairMode.Magnet },
    });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      linesRef.current = [];
    };
  }, [height]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    for (const s of linesRef.current) chart.removeSeries(s);
    const band = series.some((s) => s.kind === "dots");
    // the lines keep the upper part of the pane; the dots take the band below them
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: band ? 0.16 : 0.08 } });
    linesRef.current = series.map((s, i) => {
      if (s.kind === "dots") {
        // an invisible line on its own scale carries one round marker per point
        const dots = chart.addLineSeries({
          priceScaleId: "band",
          lineVisible: false,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          // a fixed range, so the dots sit in the middle of the band
          autoscaleInfoProvider: () => ({ priceRange: { minValue: -1, maxValue: 1 } }),
        });
        chart.priceScale("band").applyOptions({ scaleMargins: { top: 0.86, bottom: 0.02 }, visible: false });
        dots.setData(s.data.map((p) => ({ time: p.time as Time, value: p.value })));
        dots.setMarkers(
          s.data.map((p) => ({
            time: p.time as Time,
            position: "inBar" as const,
            color: p.color ?? s.color,
            shape: "circle" as const,
            size: 0.5,
          })),
        );
        return dots;
      }
      const common = {
        color: s.color,
        // the first series shows its latest value on the axis; a second line next to it would
        // print over it
        lastValueVisible: plain || i === 0,
        priceLineVisible: false,
        title: plain ? "" : s.name,
        ...(whole ? { priceFormat: { type: "price" as const, precision: 0, minMove: 1 } } : {}),
      };
      const line: ISeriesApi<"Line" | "Histogram"> =
        s.kind === "bar" ? chart.addHistogramSeries(common) : chart.addLineSeries({ ...common, lineWidth: 2 });
      line.setData(s.data.map((p) => ({ time: p.time as Time, value: p.value })));
      if (s.markers?.length) {
        line.setMarkers(
          s.markers.map((mk) => ({
            time: mk.time as Time,
            position: mk.position ?? "aboveBar",
            color: mk.color,
            shape: "circle" as const,
            size: 0.6,
            text: mk.text ?? "",
          })),
        );
      }
      if (i === 0 && threshold) {
        for (const t of Array.isArray(threshold) ? threshold : [threshold]) {
          line.createPriceLine({
            price: t.value,
            color: "#f5a524",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            // no label on the line or the axis: it covered the latest value; the card's caption
            // names the threshold
            axisLabelVisible: false,
            title: "",
          });
        }
      }
      return line;
    });
    chart.timeScale().fitContent();
  }, [series, threshold, plain, whole]);

  return <div ref={box} role="img" aria-label={label} data-testid={`mini-${label}`} className="w-full" />;
}
