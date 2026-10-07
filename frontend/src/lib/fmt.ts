// Number and text display helpers. A missing value is shown as an em dash, never as 0.
export const DASH = "—";

const priceFmt = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const intFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const fmtPrice = (v: number | null | undefined): string =>
  v === null || v === undefined ? DASH : priceFmt.format(v);

export const fmtInt = (v: number | null | undefined): string =>
  v === null || v === undefined ? DASH : intFmt.format(v);

export const fmtNum = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? DASH : v.toFixed(digits);

export const fmtPct = (v: number | null | undefined, digits = 1, sign = false): string => {
  if (v === null || v === undefined) return DASH;
  return `${sign && v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-05" -> "5 Oct 2026" (a trading day is a calendar date: no time zone conversion). */
export const fmtDay = (d: string | null | undefined): string => {
  if (!d) return DASH;
  const [y, m, dd] = d.split("-");
  const month = MONTHS[Number(m) - 1];
  return y && m && dd && month ? `${Number(dd)} ${month} ${y}` : d;
};

/** An ISO time with offset -> "6 Oct, 14:28 IST" (the API already stamps IST). */
export const fmtStamp = (iso: string | null | undefined): string => {
  if (!iso) return DASH;
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (!m) return iso;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}, ${m[4]}:${m[5]} IST`;
};

export const tone = (v: number | null | undefined): string =>
  v === null || v === undefined || v === 0 ? "text-mute" : v > 0 ? "text-up" : "text-down";

export const STRATEGY_LABEL: Record<string, string> = {
  vcp: "VCP",
  flat_base: "Flat base",
  three_weeks_tight: "3 weeks tight",
  cup_handle: "Cup & handle",
  double_bottom: "Double bottom",
};

export const strategyLabel = (id: string): string => STRATEGY_LABEL[id] ?? id;

const ACRONYM = /^(vcp|rs|sma\d*|ema\d*|atr|rsi)$/;

/** "sma150_above_sma200" -> "SMA150 above SMA200"; "A_PLUS_VCP" -> "A+ VCP"; "FLAT_BASE_LIKE" ->
 *  "Flat base like". Acronyms keep their capitals. */
export const sentence = (s: string): string => {
  const words = s
    .toLowerCase()
    .replace(/(^|[_\s])a_plus(?=$|[_\s])/g, "$1a+")
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => (ACRONYM.test(w) ? w.toUpperCase() : w === "a+" ? "A+" : w));
  const t = words.join(" ");
  return t.charAt(0).toUpperCase() + t.slice(1);
};

export const classLabel = sentence;

/** Calendar days from one ISO date to another ("2026-08-11" to "2026-08-21" is 10); null if either is missing. */
export const daysBetween = (from: string | null | undefined, to: string | null | undefined): number | null => {
  if (!from || !to) return null;
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  return Number.isNaN(a) || Number.isNaN(b) ? null : Math.round((b - a) / 86_400_000);
};
