import type { ReactNode } from "react";
import { classLabel, sentence } from "@/lib/fmt";

export function Card({
  title,
  label,
  subtitle,
  right,
  children,
  className = "",
  box = "border border-line bg-panel",
}: {
  title?: ReactNode;
  /** accessible name when the card has no visible title */
  label?: string;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  /** the border and background; the dashboard gives each card its own colour */
  box?: string;
}) {
  return (
    <section
      aria-label={title ? undefined : label}
      className={`flex flex-col rounded-lg p-4 ${box} ${className}`}
    >
      {title ? (
        <header className="mb-3 flex shrink-0 items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {subtitle ? <p className="mt-0.5 text-xs text-mute">{subtitle}</p> : null}
          </div>
          {right}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function Loading({ what = "data" }: { what?: string }) {
  return (
    <p role="status" className="py-6 text-center text-xs text-mute">
      Loading {what}…
    </p>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "Something went wrong";
  return (
    <p role="alert" className="rounded border border-down/40 bg-down/10 p-3 text-xs text-down">
      {message}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-xs text-mute">{children}</p>;
}

/** Grade badge: 3 green, 2 blue, 1 grey (the grades of STRATEGY_SPECIFICATION 6.3). */
export function GradeBadge({ classification, grade }: { classification: string; grade: number }) {
  const color =
    grade >= 3
      ? "border-up/60 bg-up/10 text-up"
      : grade === 2
        ? "border-accent/60 bg-accent/10 text-accent"
        : "border-line bg-panel2 text-mute";
  return (
    <span
      className={`inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] ${color}`}
    >
      {classLabel(classification)}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  const color =
    status === "BREAKOUT"
      ? "border-accent/60 bg-accent/10 text-accent"
      : status === "PIVOT_READY"
        ? "border-up/60 bg-up/10 text-up"
        : status === "FORMING"
          ? "border-warn/50 bg-warn/10 text-warn"
          : "border-line bg-panel2 text-mute";
  return (
    <span
      className={`inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] ${color}`}
    >
      {sentence(status)}
    </span>
  );
}

/** A coloured border and a soft gradient for a card (the palette of the Market Overview cards). */
export const TINT = {
  blue: "border-2 border-[#4aa3ff]/60 bg-gradient-to-br from-[#4aa3ff]/10 to-panel2",
  green: "border-2 border-[#26c281]/60 bg-gradient-to-br from-[#26c281]/10 to-panel2",
  violet: "border-2 border-[#8b5cf6]/60 bg-gradient-to-br from-[#8b5cf6]/10 to-panel2",
  amber: "border-2 border-[#f5a524]/60 bg-gradient-to-br from-[#f5a524]/10 to-panel2",
  cyan: "border-2 border-[#22d3ee]/60 bg-gradient-to-br from-[#22d3ee]/10 to-panel2",
  pink: "border-2 border-[#f472b6]/60 bg-gradient-to-br from-[#f472b6]/10 to-panel2",
  orange: "border-2 border-[#fb923c]/60 bg-gradient-to-br from-[#fb923c]/10 to-panel2",
} as const;
