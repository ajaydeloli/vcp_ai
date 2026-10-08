"use client";

import { usePaper } from "@/lib/api";
import { DASH, fmtDay, fmtNum, fmtPct, fmtPrice, strategyLabel, tone } from "@/lib/fmt";
import { BOX } from "./MarketHealth";
import { Card, ErrorBox, Loading } from "./ui";

export function PaperPanel() {
  const paper = usePaper();
  const p = paper.data;
  const open = p ? p.strategies.flatMap((s) => s.open.map((o) => ({ ...o, strategy: s.strategy_id }))) : [];
  const closed = p ? p.strategies.reduce((a, s) => a + s.closed, 0) : 0;
  // criteria the API could not judge yet (value null): said plainly, not shown as 0
  const notComputed = new Set(
    p?.strategies.flatMap((s) => s.criteria.filter((c) => c.value === null).map((c) => c.name)) ?? [],
  );

  return (
    <Card
      title="Paper trading"
      subtitle={
        p
          ? `Rule set ${p.rule_set}, frozen strategies. Review from ${fmtDay(p.review_from)}, at least ${p.min_closed_trades} closed trades each. No real money.`
          : undefined
      }
      box={BOX.paper}
      className="min-w-0"
    >
      {paper.isError ? (
        <ErrorBox error={paper.error} />
      ) : paper.isPending || !p ? (
        <Loading what="paper ledger" />
      ) : (
        <div className="space-y-4 text-xs">
          <table className="w-full text-left">
            <thead className="text-[11px] uppercase text-mute">
              <tr>
                <th className="py-1 font-medium">Strategy</th>
                <th className="py-1 text-right font-medium">Closed</th>
                <th className="py-1 text-right font-medium">Win rate</th>
                <th className="py-1 text-right font-medium">Avg trade</th>
                <th className="py-1 text-right font-medium">Profit factor</th>
                <th className="py-1 text-right font-medium">Open</th>
                <th className="py-1 text-right font-medium">Skipped</th>
              </tr>
            </thead>
            <tbody>
              {p.strategies.map((s) => (
                <tr key={s.strategy_id} className="border-t border-line/60">
                  <td className="py-1.5 text-ink">{strategyLabel(s.strategy_id)}</td>
                  <td className="py-1.5 text-right tabular-nums">
                    {s.closed} / {p.min_closed_trades}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{fmtPct(s.win_rate_pct, 0)}</td>
                  <td className={`py-1.5 text-right tabular-nums ${tone(s.avg_return_pct)}`}>
                    {fmtPct(s.avg_return_pct, 2, true)}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{fmtNum(s.profit_factor, 2)}</td>
                  <td className="py-1.5 text-right tabular-nums">{s.open.length}</td>
                  <td className="py-1.5 text-right tabular-nums">{s.skipped_no_slot}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {closed === 0 && open.length === 0 ? (
            <p className="rounded border border-line bg-panel2 p-3 text-mute">
              No paper trades yet. New entries are made only while the market regime is on.
            </p>
          ) : null}

          {open.length > 0 ? (
            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-mute">
                Open positions
              </h3>
              <table className="w-full text-left">
                <thead className="text-[11px] uppercase text-mute">
                  <tr>
                    <th className="py-1 font-medium">Symbol</th>
                    <th className="py-1 font-medium">List</th>
                    <th className="py-1 text-right font-medium">Entry</th>
                    <th className="py-1 text-right font-medium">Stop</th>
                    <th className="py-1 text-right font-medium">Last</th>
                    <th className="py-1 text-right font-medium">Open</th>
                  </tr>
                </thead>
                <tbody>
                  {open.map((o) => (
                    <tr key={`${o.strategy}:${o.instrument_id}`} className="border-t border-line/60">
                      <td className="py-1.5 font-medium text-ink">{o.symbol}</td>
                      <td className="py-1.5 text-mute">{strategyLabel(o.strategy)}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmtPrice(o.entry)}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmtPrice(o.stop)}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmtPrice(o.last)}</td>
                      <td className={`py-1.5 text-right tabular-nums ${tone(o.open_pct)}`}>
                        {fmtPct(o.open_pct, 2, true)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {notComputed.size > 0 ? (
            <p className="text-mute">
              Not computed yet: {[...notComputed].join("; ")}. Shown as {DASH}, not as 0.
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
}
