// A stand-in for lightweight-charts (it needs a canvas, which jsdom does not have). It records
// what the page asks the chart to draw, so tests can check series data, marks and price lines.
/* eslint-disable @typescript-eslint/no-explicit-any */
export type FakePriceLine = { price: number; title: string; color: string; lineStyle: number };

export class FakeSeries {
  data: any[] = [];
  markers: any[] = [];
  priceLines: FakePriceLine[] = [];
  constructor(
    readonly kind: string,
    readonly options: any,
  ) {}
  setData(d: any[]) {
    this.data = d;
  }
  setMarkers(m: any[]) {
    this.markers = m;
  }
  createPriceLine(o: FakePriceLine) {
    this.priceLines.push(o);
    return o;
  }
  removePriceLine(l: FakePriceLine) {
    this.priceLines = this.priceLines.filter((x) => x !== l);
  }
  priceScale() {
    return { applyOptions() {} };
  }
}

export class FakeChart {
  series: FakeSeries[] = [];
  visibleRange: any = null;
  fitted = 0;
  removed = false;
  constructor(
    readonly el: HTMLElement,
    readonly options: any,
  ) {}
  private add(kind: string, options: any) {
    const s = new FakeSeries(kind, options);
    this.series.push(s);
    return s;
  }
  addCandlestickSeries(o: any) {
    return this.add("candles", o);
  }
  addHistogramSeries(o: any) {
    return this.add("histogram", o);
  }
  addLineSeries(o: any) {
    return this.add("line", o);
  }
  removeSeries(s: FakeSeries) {
    this.series = this.series.filter((x) => x !== s);
  }
  applyOptions() {}
  timeScale() {
    return {
      setVisibleRange: (r: any) => {
        this.visibleRange = r;
      },
      fitContent: () => {
        this.fitted += 1;
      },
    };
  }
  remove() {
    this.removed = true;
  }
  of(kind: string) {
    return this.series.filter((s) => s.kind === kind);
  }
}

export const charts: FakeChart[] = [];

export const mockModule = {
  createChart: (el: HTMLElement, options: any) => {
    const c = new FakeChart(el, options);
    charts.push(c);
    return c;
  },
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Solid: 0, Dashed: 2 },
};

/** The live (not removed) charts, oldest first. */
export const liveCharts = () => charts.filter((c) => !c.removed);
export const resetCharts = () => {
  charts.length = 0;
};
