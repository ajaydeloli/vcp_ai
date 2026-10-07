"use client";

import { useMarketHealth } from "@/lib/api";
import { fmtDay } from "@/lib/fmt";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const DOT: Record<string, string> = {
  green: "bg-up",
  amber: "bg-warn",
  red: "bg-down",
  grey: "bg-mute/60",
};

/** The market read the way Minervini reads it: price action, leadership, breadth, our own trades. */
export function MarketHealth() {
  const health = useMarketHealth();
  const d = health.data;
  return (
    <Card
      title="Market health"
      subtitle={
        d?.as_of
          ? `Price action, leadership, breadth and our own paper trades · ${fmtDay(d.as_of)} · from our scanned stocks, not NIFTY`
          : "Price action, leadership, breadth and our own paper trades"
      }
      className="min-w-0"
    >
      {health.isError ? (
        <ErrorBox error={health.error} />
      ) : !d ? (
        <Loading what="market health" />
      ) : d.groups.length === 0 ? (
        <Empty>No market data to read yet.</Empty>
      ) : (
        <>
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
            {d.groups.map((g) => (
              <section key={g.id} aria-label={g.title}>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-mute">{g.title}</h3>
                <ul className="space-y-3 text-xs">
                  {g.items.map((i) => (
                    <li key={i.id} className="flex gap-2">
                      <span
                        role="img"
                        aria-label={i.status}
                        title={i.status === "grey" ? "Not enough data to read" : i.status}
                        className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${DOT[i.status] ?? DOT.grey}`}
                      />
                      <span className="min-w-0">
                        <span className="block font-medium text-ink">{i.label}</span>
                        <span className="block text-mute">{i.text}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
          <p className="mt-4 text-[11px] text-mute">
            A read for research. It changes no rule, scan or score, and does not feed the regime.
            Colours are display conventions: green healthy, amber mixed, red weak, grey not enough data.
          </p>
        </>
      )}
    </Card>
  );
}
