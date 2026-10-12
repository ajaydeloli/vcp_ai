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

/** All dashboard cards are the same height; what does not fit scrolls inside the card. */
export const SIZE = "lg:h-[23rem] lg:overflow-y-auto";

/** Each card has its own border colour, with a faint tint of it in the background. */
export const BOX = {
  index: `border-2 border-[#4aa3ff]/60 bg-gradient-to-br from-[#4aa3ff]/10 to-panel2 ${SIZE}`,
  leadership: `border-2 border-[#26c281]/60 bg-gradient-to-br from-[#26c281]/10 to-panel2 ${SIZE}`,
  breadth: `border-2 border-[#8b5cf6]/60 bg-gradient-to-br from-[#8b5cf6]/10 to-panel2 ${SIZE}`,
  feedback: `border-2 border-[#f5a524]/60 bg-gradient-to-br from-[#f5a524]/10 to-panel2 ${SIZE}`,
  stage: `border-2 border-[#22d3ee]/60 bg-gradient-to-br from-[#22d3ee]/10 to-panel2 ${SIZE}`,
  setups: `border-2 border-[#f472b6]/60 bg-gradient-to-br from-[#f472b6]/10 to-panel2 ${SIZE}`,
  paper: `border-2 border-[#fb923c]/60 bg-gradient-to-br from-[#fb923c]/10 to-panel2 ${SIZE}`,
  activity: `border-2 border-[#a3e635]/60 bg-gradient-to-br from-[#a3e635]/10 to-panel2 ${SIZE}`,
  flow: `border-2 border-[#e879f9]/60 bg-gradient-to-br from-[#e879f9]/10 to-panel2 ${SIZE}`,
  sectors: `border-2 border-[#2dd4bf]/60 bg-gradient-to-br from-[#2dd4bf]/10 to-panel2 ${SIZE}`,
  rotation: `border-2 border-[#fde047]/60 bg-gradient-to-br from-[#fde047]/10 to-panel2 lg:col-span-2`,
  score: `border-2 border-[#60a5fa]/60 bg-gradient-to-br from-[#60a5fa]/10 to-panel2 lg:col-span-2`,
};

/** Market Health Score labels, best first, and their colours. */
export const LABEL_COLOR: Record<string, string> = {
  Strong: "#26c281",
  Constructive: "#8fd14f",
  Mixed: "#f5a524",
  Weak: "#fb923c",
  Adverse: "#ef5350",
};
export const GREY = "#8a97b3";

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

export function Figure({ label, value, sub, color }: { label: string; value: string; sub?: string; color: string }) {
  return (
    <div>
      <p className="text-sm text-ink">{label}</p>
      <p className="flex flex-wrap items-baseline gap-x-1.5">
        <span className={`text-2xl font-semibold tabular-nums ${color}`}>{value}</span>
        {sub ? <span className="text-xs text-mute">{sub}</span> : null}
      </p>
    </div>
  );
}
