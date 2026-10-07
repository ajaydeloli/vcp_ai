"use client";

// A small line chart for the market overview (index vs its 50-day average; breadth with the
// regime threshold).
import { createChart, LineStyle, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";
import { CHART_THEME } from "./ChartView";

export type MiniSeries = {
  name: string;
  color: string;
  data: { time: string; value: number }[];
};

type Props = {
  series: MiniSeries[];
  height?: number;
  /** a dashed horizontal line, e.g. the breadth threshold of the regime */
  threshold?: { value: number; title: string };
  label: string;
};

export function MiniLineChart({ series, height = 120, threshold, label }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const linesRef = useRef<ISeriesApi<"Line">[]>([]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, {
      ...CHART_THEME,
      width: el.clientWidth,
      height,
      handleScroll: false,
      handleScale: false,
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
    linesRef.current = series.map((s, i) => {
      const line = chart.addLineSeries({
        color: s.color,
        lineWidth: 2,
        // the first series shows its latest value on the axis; a second line next to it would
        // print over it
        lastValueVisible: i === 0,
        priceLineVisible: false,
        title: s.name,
      });
      line.setData(s.data.map((p) => ({ time: p.time as Time, value: p.value })));
      if (i === 0 && threshold) {
        line.createPriceLine({
          price: threshold.value,
          color: "#f5a524",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          // no label on the line or the axis: it covered the latest value; the card's caption
          // names the threshold
          axisLabelVisible: false,
          title: "",
        });
      }
      return line;
    });
    chart.timeScale().fitContent();
  }, [series, threshold]);

  return <div ref={box} role="img" aria-label={label} data-testid={`mini-${label}`} className="w-full" />;
}
