"use client";

import { useWatchlist } from "@/lib/watchlist";

/** ★ button: adds the stock to, or removes it from, my watch list (kept in this browser). */
export function WatchStar({ symbol }: { symbol: string }) {
  const { has, toggle } = useWatchlist();
  const on = has(symbol);
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={on ? `Remove ${symbol} from my watch list` : `Add ${symbol} to my watch list`}
      title={on ? "In my watch list (click to remove)" : "Add to my watch list"}
      onClick={() => toggle(symbol)}
      className={`text-base leading-none ${on ? "text-warn" : "text-mute hover:text-ink"}`}
    >
      {on ? "★" : "☆"}
    </button>
  );
}
