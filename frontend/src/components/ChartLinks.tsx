"use client";

import { useState } from "react";
import { tradingViewUrl } from "@/lib/chartLinks";

const btn = "inline-flex h-7 w-7 items-center justify-center rounded border border-line text-mute hover:border-accent hover:text-ink";

/** Icon buttons: open the stock's chart on TradingView (new tab), and copy the NSE symbol to paste into Dhan or Angel One search. */
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

  const copyLabel = copied === "ok" ? `Copied ${symbol}` : copied === "fail" ? "Copy failed" : `Copy symbol ${symbol}`;
  return (
    <span className="inline-flex items-center gap-1.5">
      <a
        href={tradingViewUrl(symbol)}
        target="_blank"
        rel="noopener noreferrer"
        className={btn}
        title="Open the chart on TradingView (new tab)"
        aria-label={`Open ${symbol} chart on TradingView`}
        data-testid="tradingview-link"
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 17l5-5 4 4 6-7" />
          <path d="M14 9h4v4" />
          <path d="M3 21h18" />
        </svg>
      </a>
      <button
        type="button"
        onClick={copy}
        className={btn}
        title={copied === "ok" ? "Copied" : "Copy the symbol (paste into Dhan or Angel One search)"}
        aria-label={copyLabel}
        data-testid="copy-symbol"
      >
        {copied === "ok" ? (
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 text-up" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12l5 5 9-10" />
          </svg>
        ) : (
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V5a2 2 0 0 1 2-2h8" />
          </svg>
        )}
      </button>
    </span>
  );
}
