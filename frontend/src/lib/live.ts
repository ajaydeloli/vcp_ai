// Words for live prices. Live values are display only; they are always labelled as live (with
// their age) or as the close of a named session, and a price the feed does not have is "not
// available", never 0.
import { fmtDay } from "./fmt";
import type { LiveFeed, LiveQuote } from "./schemas";

/** 12 -> "12 s"; 200 -> "3 min". */
export const fmtAge = (sec: number): string => (sec < 120 ? `${sec} s` : `${Math.round(sec / 60)} min`);

/** The label under a live number: "Live, delayed 12 s", "Stale, 3 min old" or "Close of 6 Oct 2026". */
export function liveLabel(q: Pick<LiveQuote, "mode" | "delay_seconds" | "session_date">): string | null {
  if (q.mode === "live") return q.delay_seconds === null ? "Live" : `Live, delayed ${fmtAge(q.delay_seconds)}`;
  if (q.mode === "stale") return q.delay_seconds === null ? "Stale" : `Stale, ${fmtAge(q.delay_seconds)} old`;
  if (q.mode === "closed") return `Close of ${fmtDay(q.session_date)}`;
  return null;
}

export const NOT_AVAILABLE = "Not available";

/** Why a price is missing, in plain words. */
export const unavailable = (q: Pick<LiveQuote, "reason"> | undefined): string =>
  q?.reason ? `${NOT_AVAILABLE}: ${q.reason}` : NOT_AVAILABLE;

/** What to tell the person when the feed is not simply live or closed; null when all is well. */
export function feedNotice(feed: LiveFeed | undefined): string | null {
  if (!feed) return null;
  switch (feed.state) {
    case "token_needed":
      return feed.message ?? "Live feed needs a fresh token";
    case "rate_limited":
      return feed.message ?? `${feed.provider} rate limit reached`;
    case "error":
      return feed.message ?? `${feed.provider} did not answer`;
    case "stale":
      return `Live prices from ${feed.provider} are out of date`;
    case "disabled":
      return feed.message ?? "Live prices are switched off";
    case "starting":
      return "Live prices are starting";
    default:
      return null;
  }
}

/** A one-word state for the status bar. */
export function feedState(feed: LiveFeed | undefined): string {
  if (!feed) return "Loading";
  switch (feed.state) {
    case "live":
      return `Live (${feed.provider})`;
    case "closed":
      return feed.market === "pre_open" ? "Opens 09:15" : "Market closed";
    case "token_needed":
      return "Needs a fresh token";
    case "rate_limited":
      return "Rate limited";
    case "stale":
      return "Stale";
    case "starting":
      return "Starting";
    case "disabled":
      return "Off";
    default:
      return "Problem";
  }
}
