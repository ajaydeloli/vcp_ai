import { describe, expect, it } from "vitest";
import { DEFAULT_CHART, parseChart } from "@/lib/chartSettings";
import { aggregate, bollinger, ema, heikinAshi, lastPerBar, macd, periodOf, rangeStart, rsi, rsLabels, rsLine, rsNewHighs, sma, weekKey, type OBar } from "@/lib/indicators";

const bar = (day: string, o: number, h: number, l: number, c: number, v = 100): OBar => ({ day, open: o, high: h, low: l, close: c, volume: v });

describe("chart indicators", () => {
  it("simple and exponential averages start once enough bars exist", () => {
    expect(sma([1, 2, 3, 4], 2)).toEqual([null, 1.5, 2.5, 3.5]);
    expect(sma([1, null, 3, 4], 2)).toEqual([null, null, null, 3.5]);
    const e = ema([1, 2, 3, 4, 5], 3);
    expect(e.slice(0, 3)).toEqual([null, null, 2]); // seeded with the simple average
    expect(e[3]).toBeCloseTo(3); // 4 * 0.5 + 2 * 0.5
    expect(e[4]).toBeCloseTo(4);
  });

  it("RSI is 100 when every bar closes up and about 50 when they alternate evenly", () => {
    const up = Array.from({ length: 20 }, (_, i) => 100 + i);
    expect(rsi(up)[14]).toBe(100);
    expect(rsi(up)[13]).toBeNull();
    const zig = Array.from({ length: 60 }, (_, i) => (i % 2 ? 101 : 100));
    expect(rsi(zig)[59]!).toBeGreaterThan(40);
    expect(rsi(zig)[59]!).toBeLessThan(60);
  });

  it("MACD, its signal and histogram line up; Bollinger bands close on a flat series", () => {
    const v = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 3) * 5);
    const m = macd(v);
    expect(m.macd[24]).toBeNull();
    expect(m.macd[25]).not.toBeNull();
    expect(m.signal[33]).not.toBeNull(); // 26 + 9 - 2
    expect(m.hist[59]).toBeCloseTo(m.macd[59]! - m.signal[59]!);
    const flat = bollinger(Array(25).fill(10));
    expect(flat.upper[24]).toBe(10);
    expect(flat.lower[24]).toBe(10);
  });

  it("weekly and monthly bars, and where each session's marks go", () => {
    const daily = [
      bar("2026-10-29", 10, 12, 9, 11), // Thu
      bar("2026-10-30", 11, 13, 10, 12), // Fri
      bar("2026-11-02", 12, 15, 11, 14), // Mon
      bar("2026-11-04", 14, 14, 8, 9, 50), // Wed
    ];
    expect(weekKey("2026-11-04")).toBe("2026-11-02");
    const w = aggregate(daily, "W");
    expect(w).toEqual([bar("2026-10-29", 10, 13, 9, 12, 200), bar("2026-11-02", 12, 15, 8, 9, 150)]);
    const m = aggregate(daily, "M");
    expect(m.map((b) => b.day)).toEqual(["2026-10-29", "2026-11-02"]);
    expect(periodOf(daily, "W").get("2026-11-04")).toBe("2026-11-02");
    expect(periodOf(daily, "W").get("2026-10-30")).toBe("2026-10-29");
    expect(aggregate(daily, "D")).toBe(daily);
  });

  it("Heikin Ashi bars average the prices", () => {
    const [a, b] = heikinAshi([bar("d1", 10, 12, 8, 11), bar("d2", 11, 14, 10, 13)]);
    expect(a!.close).toBe(10.25);
    expect(a!.open).toBe(10.5);
    expect(b!.open).toBeCloseTo((10.5 + 10.25) / 2);
    expect(b!.high).toBe(14);
  });

  it("ranges count back calendar time from the last bar", () => {
    expect(rangeStart("2026-11-06", "1Y")).toBe("2025-11-06");
    expect(rangeStart("2026-11-06", "3M")).toBe("2026-08-06");
    expect(rangeStart("2026-11-06", "YTD")).toBe("2026-01-01");
    expect(rangeStart("2026-11-06", "ALL")).toBeNull();
  });

  it("saved settings are checked; anything unknown falls back to the defaults", () => {
    expect(parseChart(null)).toEqual(DEFAULT_CHART);
    expect(parseChart("not json")).toEqual(DEFAULT_CHART);
    expect(parseChart(JSON.stringify({ interval: "W", type: "area", range: "5Y", indicators: ["rsi", "nope"], log: true })))
      .toEqual({ interval: "W", type: "area", range: "5Y", indicators: ["rsi"], log: true, benchmark: "Nifty 500" });
    expect(parseChart(JSON.stringify({ interval: "X", type: "pie" })).interval).toBe("D");
  });

  it("the RS line is the stock over the index, 100 at its start, per week at the week's last session", () => {
    const daily = [bar("2026-10-29", 10, 10, 10, 10), bar("2026-10-30", 11, 11, 11, 11), bar("2026-11-02", 12, 12, 12, 12), bar("2026-11-04", 15, 15, 15, 15)];
    const idx = [
      { day: "2026-10-29", close: 100 },
      { day: "2026-10-30", close: 100 },
      { day: "2026-11-02", close: 120 },
      { day: "2026-11-04", close: 100 },
    ];
    expect(rsLine(daily, idx, daily, "D").map((v) => Number(v!.toFixed(2)))).toEqual([100, 110, 100, 150]);
    const w = aggregate(daily, "W");
    expect(rsLine(daily, idx, w, "W")).toEqual([100, (15 / 100 / (11 / 100)) * 100]);
    expect(rsLine(daily, [], daily, "D")).toEqual([null, null, null, null]);
    expect(rsNewHighs([100, 101, 99, 102, 102, 103], 2)).toEqual([3, 5]);
  });

  it("RS ranks per bar, and labels on the first dot of each run and on the last bar", () => {
    const daily = [bar("2026-10-29", 1, 1, 1, 1), bar("2026-10-30", 1, 1, 1, 1), bar("2026-11-02", 1, 1, 1, 1)];
    const ranks = [
      { day: "2026-10-30", value: 80 },
      { day: "2026-10-29", value: 75 },
    ];
    expect(lastPerBar(ranks, daily, daily, "D")).toEqual([75, 80, null]);
    expect(lastPerBar(ranks, daily, aggregate(daily, "W"), "W")).toEqual([80, null]);
    // new highs at bars 2, 3 (one run) and 6; the last bar is 7
    expect(rsLabels([2, 3, 6], [70, 71, 72, 73, 74, 75, 76, 87], 7)).toEqual([
      { index: 2, dot: true, text: "RS 72" },
      { index: 3, dot: true, text: "" },
      { index: 6, dot: true, text: "RS 76" },
      { index: 7, dot: false, text: "RS Rating 87" },
    ]);
    expect(rsLabels([4], [null, null, null, null, null], 4)).toEqual([{ index: 4, dot: true, text: "" }]);
    // with a gap of 3 bars: the run at 6 is too close to the label at 2 and to the last bar
    expect(rsLabels([2, 3, 6], [70, 71, 72, 73, 74, 75, 76, 87], 7, 3).map((l) => l.text)).toEqual(["RS 72", "", "", "RS Rating 87"]);
  });
});
