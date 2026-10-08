"use client";

import { useLiveStatus } from "@/lib/api";
import { DASH, fmtInt, fmtNum, fmtPct, fmtPrice, tone } from "@/lib/fmt";
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

/** The live price box of the stock page, labelled "Live, delayed N s" or "Close of <date>", with the
 *  day's open, high, low and volume where the feed has them (a dash where it does not). */
export function LiveLine({ q }: { q: LiveQuote | undefined }) {
  if (!q) return null;
  const box = "py-3";
  if (!q.available || q.last_price === null) {
    return (
      <div className={box} data-testid="live-line">
        <p className="text-[11px] uppercase tracking-wide text-live">Live</p>
        <p className="mt-1 text-sm text-mute">Live price: {unavailable(q).toLowerCase()}</p>
      </div>
    );
  }
  const day: [string, string][] = [
    ["Open", fmtPrice(q.open)],
    ["High", fmtPrice(q.high)],
    ["Low", fmtPrice(q.low)],
    ["Volume", fmtInt(q.volume)],
  ];
  return (
    <div className={box} data-testid="live-line">
      <p className="text-[11px] uppercase tracking-wide text-live">{q.mode === "closed" ? "Last" : "Live"}</p>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3">
        <span className="text-base font-semibold tabular-nums text-ink">{fmtPrice(q.last_price)}</span>
        <span className={`text-sm tabular-nums ${tone(q.change)}`}>
          {q.change === null ? DASH : `${q.change > 0 ? "+" : ""}${fmtNum(q.change, 2)}`} ({fmtPct(q.change_pct, 2, true)})
        </span>
      </div>
      <p className="mt-1 text-[11px] text-mute">
        {liveLabel(q)} · {q.source ?? DASH} · display only
      </p>
      <dl className="mt-3 grid grid-cols-4 divide-x divide-line border-t border-line pt-2 text-center">
        {day.map(([k, v]) => (
          <div key={k} className="px-1">
            <dt className="text-[11px] text-mute">{k}</dt>
            <dd className="text-base font-semibold tabular-nums text-ink">{v}</dd>
          </div>
        ))}
      </dl>
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
