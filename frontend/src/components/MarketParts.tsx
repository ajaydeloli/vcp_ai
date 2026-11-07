"use client";

// Pieces shared by the Market Overview cards (MarketHealth, MarketFlow, …).
import type { ReactNode } from "react";

export const C = { ink: "#e6e9ef", up: "#26a69a", down: "#ef5350", warn: "#f5a524", violet: "#a78bfa", blue: "#5b9dff" };

/** NIFTY 50 / 500 on the index chart: steps of the validated reference palette (blue, aqua) that
 *  pass every colour check together on the dark card surface, including colour-blind vision. */
export const NSE = { nifty50: "#3987e5", nifty500: "#1baf7a" };

export function Legend({ items }: { items: [string, string][] }) {
  return (
    <p className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-mute">
      {items.map(([name, color]) => (
        <span key={name} className="inline-flex items-center gap-1">
          <span className="inline-block h-0.5 w-3" style={{ background: color }} />
          {name}
        </span>
      ))}
    </p>
  );
}

/** All dashboard cards are the same height. A data card (a table, a list) scrolls inside;
 *  a card with a chart never scrolls: it shows only what fits (FRONTEND_SPECIFICATION §67.35). */
export const SIZE = "lg:h-[23rem] lg:overflow-y-auto";
const FIXED = "lg:h-[23rem] lg:overflow-hidden";

/** Each card has its own border colour, with a faint tint of it in the background. */
/** The Market Overview cards: plain, one neutral border and the panel colour (owner,
 *  2026-11-07: no coloured borders or tints). Only the sizes differ. */
const PLAIN = "border border-line bg-panel";
export const BOX = {
  index: `${PLAIN} ${FIXED}`,
  leadership: `${PLAIN} ${FIXED}`,
  breadth: `${PLAIN} ${FIXED}`,
  feedback: `${PLAIN} ${FIXED}`,
  stage: `${PLAIN} ${FIXED}`,
  setups: `${PLAIN} ${SIZE}`,
  paper: `${PLAIN} ${SIZE}`,
  activity: `${PLAIN} ${SIZE}`,
  flow: `${PLAIN} ${FIXED}`,
  sectors: `${PLAIN} ${SIZE}`,
  volume: `${PLAIN} ${FIXED}`,
  participation: `${PLAIN} ${FIXED}`,
  highs: `${PLAIN} ${SIZE}`,
  rotation: `${PLAIN} lg:row-span-2 lg:h-[47rem] lg:overflow-hidden`,
  score: `${PLAIN} lg:col-span-2`,
};

/** Market Health Score labels, best first, and their colours. */
export const LABEL_COLOR: Record<string, string> = {
  Strong: "#26c281",
  Constructive: "#8fd14f",
  Mixed: "#f5a524",
  Weak: "#fb923c",
  Adverse: "#ef5350",
};
export const GREY = "#8e8e96";

/** The shadow traffic light (logged, no trading effect). */
export const EXPOSURE: Record<string, { text: string; color: string }> = {
  full: { text: "Full size", color: "#26c281" },
  half: { text: "Half size", color: "#f5a524" },
  none: { text: "No new buys", color: "#ef5350" },
};

/** `fill`: the card is a column whose title stays put while one child (with `min-h-0 flex-1
 *  overflow-auto`) scrolls, like the Recent activity card. */
export function Inner({ title, box, children, fill = false }: { title: string; box: string; children: ReactNode; fill?: boolean }) {
  return (
    <section aria-label={title} className={`min-w-0 rounded-lg p-4 ${fill ? "flex flex-col" : ""} ${box}`}>
      <h3 className="mb-3 shrink-0 text-xs font-semibold uppercase tracking-wide text-mute">{title}</h3>
      {children}
    </section>
  );
}

export function Figure({ label, value, sub, color, small = false }: { label: string; value: string; sub?: string; color: string; small?: boolean }) {
  return (
    <div>
      <p className={small ? "text-xs leading-tight text-ink" : "text-sm text-ink"}>{label}</p>
      <p className="flex flex-wrap items-baseline gap-x-1.5">
        <span className={`${small ? "text-xl" : "text-2xl"} font-semibold tabular-nums ${color}`}>{value}</span>
        {sub ? <span className={small ? "text-[10px] leading-tight text-mute" : "text-xs text-mute"}>{sub}</span> : null}
      </p>
    </div>
  );
}
