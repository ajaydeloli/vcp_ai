"use client";

import { usePaper } from "@/lib/api";
import { DASH, fmtDay, fmtNum, strategyLabel } from "@/lib/fmt";
import { PaperPanel } from "./PaperPanel";
import { Page } from "./Page";
import { Card, ErrorBox, Loading, TINT } from "./ui";

/** The paper ledger in full: results, open positions and the review gates of each strategy. */
export function PaperTrading() {
  const paper = usePaper();
  const p = paper.data;
  return (
    <Page active="Paper Trading" title="Paper Trading">
      <PaperPanel />
      <Card
        title="Review gates"
        subtitle={p ? `Each strategy is judged from ${fmtDay(p.review_from)}. A gate with no value yet shows ${DASH}, not 0.` : undefined}
        box={TINT.violet}
      >
        {paper.isError ? (
          <ErrorBox error={paper.error} />
        ) : !p ? (
          <Loading what="review gates" />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {p.strategies.map((s) => (
              <div key={s.strategy_id} className="rounded-lg border border-line/60 p-3">
                <h3 className="text-sm font-semibold text-ink">{strategyLabel(s.strategy_id)}</h3>
                <p className="mb-2 text-[11px] text-mute">Ledger through {fmtDay(s.ledger_through)}</p>
                <ul className="divide-y divide-line text-xs">
                  {s.criteria.map((c) => (
                    <li key={c.name} className="flex items-center justify-between gap-3 py-1.5">
                      <span>
                        <span className="text-ink">{c.name}</span>
                        <span className="block text-[11px] text-mute">needs {c.required}</span>
                      </span>
                      <span className="text-right">
                        <span className="tabular-nums text-ink">{fmtNum(c.value, 2)}</span>
                        <span className={`block text-[11px] ${c.met === null ? "text-mute" : c.met ? "text-up" : "text-down"}`}>
                          {c.met === null ? "not yet judged" : c.met ? "met" : "not met"}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-[11px] text-mute">
                  Divergences from the rules: {s.divergences} · skipped, no free slot: {s.skipped_no_slot}
                </p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </Page>
  );
}
