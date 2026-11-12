"use client";

import { useLiveQuotes, usePaper } from "@/lib/api";
import { DASH, fmtDay, fmtNum, fmtPct, fmtPrice, strategyLabel, tone } from "@/lib/fmt";
import type { Paper } from "@/lib/schemas";
import { LiveCell, LiveGain, LiveNotice } from "./Live";
import { PaperPanel } from "./PaperPanel";
import { Page } from "./Page";
import { Card, ErrorBox, Loading } from "./ui";

/** The paper ledger in full: results, open positions and the review gates of each strategy. */
export function PaperTrading() {
  const paper = usePaper();
  const p = paper.data;
  return (
    <Page active="Paper Trading" title="Paper Trading">
      <LiveNotice />
      <PaperPanel />
      <Card
        title="Review gates"
        subtitle={p ? `Each strategy is judged from ${fmtDay(p.review_from)}. A gate with no value yet shows ${DASH}, not 0.` : undefined}
       
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
      {p && p.research.length > 0 ? <Research p={p} /> : null}
    </Page>
  );
}

/** Research ledgers: rule variants replayed next to the frozen rules, on the same signals and
 *  days. Shown apart; they never count in the review above. */
function Research({ p }: { p: Paper }) {
  const symbols = p.research.flatMap((r) => r.open.map((o) => o.symbol));
  const live = useLiveQuotes(symbols, symbols.length > 0); // display only
  return (
    <Card
      title="Research ledgers"
      subtitle="Rule variants tracked next to the frozen rules, on the same signals and days. Not part of the review. No real money."
    >
      <div className="space-y-4 text-xs">
        {p.research.map((r) => (
          <section key={r.rule_set} aria-label={r.rule_set}>
            <h3 className="text-sm font-semibold text-ink">
              {strategyLabel(r.strategy_id)}: {r.label}
            </h3>
            <p className="mb-2 text-[11px] text-mute">
              {`Rule set ${r.rule_set} · rule fixed ${fmtDay(r.decided)} · from ${fmtDay(r.start)} · ledger through ${fmtDay(r.ledger_through)}`}
            </p>
            <table className="w-full text-left">
              <thead className="text-[11px] uppercase text-mute">
                <tr>
                  <th className="py-1 font-medium">Closed</th>
                  <th className="py-1 text-right font-medium">Win rate</th>
                  <th className="py-1 text-right font-medium">Avg trade</th>
                  <th className="py-1 text-right font-medium">Profit factor</th>
                  <th className="py-1 text-right font-medium">Open</th>
                  <th className="py-1 text-right font-medium">Skipped</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-line/60">
                  <td className="py-1.5 tabular-nums">
                    {r.closed} / {p.min_closed_trades}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{fmtPct(r.win_rate_pct, 0)}</td>
                  <td className={`py-1.5 text-right tabular-nums ${tone(r.avg_return_pct)}`}>{fmtPct(r.avg_return_pct, 2, true)}</td>
                  <td className="py-1.5 text-right tabular-nums">{fmtNum(r.profit_factor, 2)}</td>
                  <td className="py-1.5 text-right tabular-nums">{r.open.length}</td>
                  <td className="py-1.5 text-right tabular-nums">{r.skipped_no_slot}</td>
                </tr>
              </tbody>
            </table>
            {r.open.length > 0 ? (
              <ul className="mt-2 divide-y divide-line/60">
                {r.open.map((o) => (
                  <li key={o.instrument_id} className="flex flex-wrap items-center gap-x-4 gap-y-0.5 py-1.5">
                    <a href={`/chart/${encodeURIComponent(o.symbol)}`} className="font-medium text-accent hover:underline">
                      {o.symbol}
                    </a>
                    <span className="text-mute">{`since ${fmtDay(o.entry_day)} at ${fmtPrice(o.entry)}`}</span>
                    <span className="text-mute">{`stop ${o.stop === null ? DASH : fmtPrice(o.stop)}`}</span>
                    <span className={`tabular-nums ${tone(o.open_pct)}`} title="ledger result at the last close">
                      {fmtPct(o.open_pct, 2, true)}
                    </span>
                    <span className="text-mute">
                      live <LiveCell q={live.bySymbol.get(o.symbol.toUpperCase())} />
                    </span>
                    <span className="text-mute">
                      live P/L <LiveGain q={live.bySymbol.get(o.symbol.toUpperCase())} entry={o.entry} stop={o.stop} entryDay={o.entry_day} />
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        ))}
      </div>
    </Card>
  );
}
