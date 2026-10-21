import { describe, expect, it } from "vitest";
import { tradingViewSymbol, tradingViewUrl } from "@/lib/chartLinks";

describe("chart links", () => {
  it("maps NSE symbols to TradingView tickers", () => {
    expect(tradingViewSymbol("STOVEKRAFT")).toBe("NSE:STOVEKRAFT");
    expect(tradingViewSymbol("M&M")).toBe("NSE:M_M");
    expect(tradingViewSymbol("bajaj-auto")).toBe("NSE:BAJAJ_AUTO");
  });

  it("builds an encoded chart URL", () => {
    expect(tradingViewUrl("M&M")).toBe("https://www.tradingview.com/chart/?symbol=NSE%3AM_M");
  });
});
