// The number style the owner picked (preferences, 2026-10-22): Indian grouping 1,23,456.78
// (default) or international 123,456.78. Read once from this browser's storage and cached;
// prefs.ts clears the cache when the preference changes.
import { PREFS_KEY } from "./themeBoot";

let cached: string | null = null;

export function numberLocale(): string {
  if (cached !== null) return cached;
  let loc = "en-IN";
  try {
    if (typeof window !== "undefined" && JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}").numbers === "intl") loc = "en-US";
  } catch {
    /* default */
  }
  cached = loc;
  return loc;
}

export const resetNumberLocale = () => {
  cached = null;
};
