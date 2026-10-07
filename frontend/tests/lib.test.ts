import { describe, expect, it } from "vitest";
import {
  buildMarkers,
  buildPriceLines,
  rangeFrom,
  toCandles,
  toSma,
  toVolume,
} from "@/lib/chartData";
import { classLabel, daysBetween, sentence, DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, fmtStamp, tone } from "@/lib/fmt";
import type { Bar, StockSetup } from "@/lib/schemas";
import { unmetByTier } from "@/components/SetupOverview";
import { pageNumbers } from "@/lib/lists";

const bar = (day: string, o: Partial<Bar> = {}): Bar => ({
  day, open: 10, high: 12, low: 9, close: 11, volume: 1000, sma20: null, sma50: null, sma200: null, ...o,
}); // fmt: skip

describe("display helpers: a missing value is a dash, never 0", () => {
  it("numbers", () => {
    for (const f of [fmtPrice, fmtInt, fmtNum, fmtPct]) expect(f(null)).toBe(DASH);
    expect(fmtPrice(1236.4)).toBe("1,236.40");
    expect(fmtPrice(0)).toBe("0.00"); // a real zero is shown
    expect(fmtInt(1271)).toBe("1,271");
    expect(fmtPct(1.84, 2, true)).toBe("+1.84%");
    expect(fmtPct(-1.84, 2, true)).toBe("-1.84%");
    expect(fmtNum(91.234)).toBe("91.2");
  });

  it("dates and times keep the calendar day and the IST clock", () => {
    expect(fmtDay("2026-10-05")).toBe("5 Oct 2026");
    expect(fmtDay(null)).toBe(DASH);
    expect(fmtStamp("2026-10-06T14:28:38.126266+05:30")).toBe("6 Oct, 14:28 IST");
    expect(fmtStamp(null)).toBe(DASH);
  });

  it("tone and labels", () => {
    expect(tone(1)).toBe("text-up");
    expect(tone(-1)).toBe("text-down");
    expect(tone(null)).toBe("text-mute");
    expect(classLabel("A_PLUS_VCP")).toBe("A+ VCP");
    expect(classLabel("FLAT_BASE_LIKE")).toBe("Flat base like");
    expect(classLabel("VCP_LIKE")).toBe("VCP like");
    expect(classLabel("VCP")).toBe("VCP");
    expect(sentence("sma150_above_sma200")).toBe("SMA150 above SMA200");
    expect(sentence("rs_rank")).toBe("RS rank");
    expect(sentence("PIVOT_READY")).toBe("Pivot ready");
  });
});

describe("chart series", () => {
  const bars = [
    bar("2026-10-01", { sma20: 10.5 }),
    bar("2026-10-02", { open: null }),
    bar("2026-10-05", { sma20: 10.6, sma50: 10, volume: null }),
  ];

  it("candles skip bars without a full OHLC", () => {
    expect(toCandles(bars).map((c) => c.time)).toEqual(["2026-10-01", "2026-10-05"]);
  });

  it("volume skips missing volume and colours by direction", () => {
    const v = toVolume([bar("2026-10-01"), bar("2026-10-02", { close: 9 }), ...bars.slice(2)]);
    expect(v).toHaveLength(2);
    expect(v[0]?.color).not.toBe(v[1]?.color);
  });

  it("an average is drawn only where it exists (null is not drawn as 0)", () => {
    expect(toSma(bars, "sma20")).toEqual([
      { time: "2026-10-01", value: 10.5 },
      { time: "2026-10-05", value: 10.6 },
    ]);
    expect(toSma(bars, "sma200")).toEqual([]);
  });

  it("range buttons show the last N bars; All and short history show everything", () => {
    const many = Array.from({ length: 300 }, (_, i) => bar(`d${String(i).padStart(3, "0")}`));
    expect(rangeFrom(many, "3M")).toBe("d237");
    expect(rangeFrom(many, "1Y")).toBe("d048");
    expect(rangeFrom(many, "ALL")).toBeNull();
    expect(rangeFrom(many.slice(0, 50), "3M")).toBeNull();
  });
});

describe("marks of a setup", () => {
  const days = ["2026-08-01", "2026-08-03", "2026-08-20", "2026-09-07", "2026-10-05"];
  const bars = days.map((d) => bar(d, { high: d === "2026-08-03" ? 125 : 12, low: d === "2026-08-20" ? 7 : 9 }));
  const setup: StockSetup = {
    strategy_id: "x", classification: "C", grade: 2, status: "FORMING", eligible: true, score: 1,
    pivot: 126, stop: 108, base_start: "2026-08-01", base_end: null, base_depth_pct: 12,
    breakout_date: "2026-10-05",
    points: [
      { label: "L", day: "2026-08-03", price: 125 },
      { label: "B", day: "2026-08-20", price: 7 },
    ],
    contractions: [
      { sequence: 1, peak_date: "2026-08-03", peak_price: 125, trough_date: "2026-09-07", trough_price: 9, depth_pct: 9.2 },
    ],
    score_parts: [], details: {},
  }; // fmt: skip

  it("base start, contractions, points and the breakout, in time order", () => {
    const m = buildMarkers(bars, setup);
    expect(m.map((x) => `${x.time}:${x.text}`)).toEqual([
      "2026-08-01:Base start",
      "2026-08-03:P1",
      "2026-08-03:L",
      "2026-08-20:B",
      "2026-09-07:T1 9.2%",
      "2026-10-05:Breakout",
    ]);
  });

  it("a point on the bar's high goes above it, otherwise below", () => {
    const m = buildMarkers(bars, setup);
    expect(m.find((x) => x.text === "L")?.position).toBe("aboveBar");
    expect(m.find((x) => x.text === "B")?.position).toBe("belowBar");
  });

  it("a mark on a day with no bar is dropped, and no setup gives no marks", () => {
    expect(buildMarkers(bars.slice(0, 1), setup).map((x) => x.text)).toEqual(["Base start"]);
    expect(buildMarkers(bars, null)).toEqual([]);
  });

  it("pivot and stop lines only where the values exist", () => {
    expect(buildPriceLines(setup).map((l) => [l.title, l.price, l.dashed])).toEqual([
      ["Pivot", 126, false],
      ["Stop", 108, true],
    ]);
    expect(buildPriceLines({ ...setup, pivot: null, stop: null })).toEqual([]);
  });
});

describe("unmet rules of a setup", () => {
  it("lists the rules missed for each higher tier, skips empty tiers and other shapes", () => {
    const d = { unmet_rules: { A_PLUS_VCP: ["final_contraction", "dryup"], VCP: [], VCP_LIKE: ["x"] } };
    expect(unmetByTier(d)).toEqual([["A_PLUS_VCP", ["final_contraction", "dryup"]], ["VCP_LIKE", ["x"]]]);
    expect(unmetByTier({ unmet_rules: {} })).toEqual([]);
    expect(unmetByTier({ unmet_rules: ["a"] })).toEqual([]);
    expect(unmetByTier({})).toEqual([]);
  });
});

describe("page numbers", () => {
  it("shows every page when there are few, and gaps when there are many", () => {
    expect(pageNumbers(0, 1)).toEqual([0]);
    expect(pageNumbers(0, 3)).toEqual([0, 1, 2]);
    expect(pageNumbers(5, 12)).toEqual([0, "…", 3, 4, 5, 6, 7, "…", 11]);
    expect(pageNumbers(0, 12)).toEqual([0, 1, 2, "…", 11]);
    expect(pageNumbers(11, 12)).toEqual([0, "…", 9, 10, 11]);
  });
});

describe("days between dates", () => {
  it("counts calendar days and is null for a missing or bad date", () => {
    expect(daysBetween("2026-08-11", "2026-08-21")).toBe(10);
    expect(daysBetween("2026-12-30", "2027-01-02")).toBe(3);
    expect(daysBetween(null, "2026-08-21")).toBeNull();
    expect(daysBetween("2026-08-11", "nope")).toBeNull();
  });
});
