"use client";

import { useLiveStatus } from "@/lib/api";
import { DASH, fmtNum, fmtPct, fmtPrice, tone } from "@/lib/fmt";
import { feedNotice, liveLabel, unavailable } from "@/lib/live";
import type { LiveQuote } from "@/lib/schemas";

/** A live price and its day change for a table cell. Display only: it never replaces or changes
 *  a stored value, and it is not sortable. A price the feed does not have is a dash. */
export function LiveCell({ q }: { q: LiveQuote | undefined }) {
  if (!q) return <span className="text-mute">{DASH}</span>;
  if (!q.available || q.last_price === null) {
    return (
      <span className="text-mute" title={unavailable(q)} data-testid={`live-${q.symbol}`}>
        {DASH}
      </span>
    );
  }
  const label = liveLabel(q);
  return (
    <span className="whitespace-nowrap" title={label ?? undefined} data-testid={`live-${q.symbol}`}>
      <span className="tabular-nums text-ink">{fmtPrice(q.last_price)}</span>{" "}
      <span className={`tabular-nums ${tone(q.change_pct)}`}>{fmtPct(q.change_pct, 2, true)}</span>
      {q.mode !== "closed" ? <span className="ml-1 text-[10px] text-live">●</span> : null}
    </span>
  );
}

/** The live price beside the stored close on the stock page, labelled "Live, delayed N s" or
 *  "Close of <date>". */
export function LiveLine({ q }: { q: LiveQuote | undefined }) {
  if (!q) return null;
  if (!q.available || q.last_price === null) {
    return (
      <p className="mt-1 text-[11px] text-mute" data-testid="live-line">
        Live price: {unavailable(q).toLowerCase()}
      </p>
    );
  }
  return (
    <div className="mt-2 flex flex-wrap items-baseline gap-x-3" data-testid="live-line">
      <span className="text-[11px] uppercase tracking-wide text-live">{q.mode === "closed" ? "Last" : "Live"}</span>
      <span className="text-xl font-semibold tabular-nums text-ink">{fmtPrice(q.last_price)}</span>
      <span className={`text-sm tabular-nums ${tone(q.change)}`}>
        {q.change === null ? DASH : `${q.change > 0 ? "+" : ""}${fmtNum(q.change, 2)}`} ({fmtPct(q.change_pct, 2, true)})
      </span>
      <span className="text-[11px] text-mute">{liveLabel(q)} · {q.source ?? DASH} · display only</span>
    </div>
  );
}

/** A banner when the live feed needs attention (fresh token, rate limit, error, stale); nothing
 *  when it is live or the market is closed. */
export function LiveNotice() {
  const status = useLiveStatus();
  const notice = feedNotice(status.data?.feed);
  if (!notice || status.data?.feed.state === "starting") return null;
  return (
    <p role="status" className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn" data-testid="live-notice">
      {notice}. Prices below fall back to the stored end-of-day data.
    </p>
  );
}
