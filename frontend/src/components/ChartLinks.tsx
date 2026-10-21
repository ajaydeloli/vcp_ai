"use client";

import { useState } from "react";
import { tradingViewUrl } from "@/lib/chartLinks";

/** "TradingView ↗" opens the stock's chart in a new tab; "Copy symbol" puts the NSE symbol on the clipboard to paste into Dhan or Angel One search. */
export function ChartLinks({ symbol }: { symbol: string }) {
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(symbol);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
    window.setTimeout(() => setCopied(null), 1500);
  }

  return (
    <span className="inline-flex items-center gap-2 text-xs">
      <a
        href={tradingViewUrl(symbol)}
        target="_blank"
        rel="noopener noreferrer"
        className="rounded border border-line px-2 py-0.5 text-accent hover:border-accent"
        title="Open this stock's chart on TradingView (new tab)"
        data-testid="tradingview-link"
      >
        TradingView ↗
      </a>
      <button
        type="button"
        onClick={copy}
        className="rounded border border-line px-2 py-0.5 text-mute hover:text-ink"
        title="Copy the NSE symbol, then paste it into Dhan or Angel One search"
        data-testid="copy-symbol"
      >
        {copied === "ok" ? "Copied ✓" : copied === "fail" ? "Copy failed" : "Copy symbol"}
      </button>
    </span>
  );
}
