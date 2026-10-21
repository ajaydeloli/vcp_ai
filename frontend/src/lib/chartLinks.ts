// Links out to an external chart (owner decision 2026-10-21). Dhan and Angel One do not put the
// symbol in their chart address, so the link goes to TradingView (Dhan can be connected there as a
// broker) and a "Copy symbol" button covers any other platform. Nothing is fetched from these sites.

/** TradingView's NSE ticker: "&" and "-" become "_" (M&M -> NSE:M_M, BAJAJ-AUTO -> NSE:BAJAJ_AUTO). */
export function tradingViewSymbol(symbol: string): string {
  return `NSE:${symbol.trim().toUpperCase().replace(/[&-]/g, "_")}`;
}

export function tradingViewUrl(symbol: string): string {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tradingViewSymbol(symbol))}`;
}
