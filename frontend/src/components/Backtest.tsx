"use client";

import { useMemo, useState } from "react";
import { useBacktests } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, sentence, strategyLabel, STRATEGY_LABEL, tone } from "@/lib/fmt";
import type { BacktestRun } from "@/lib/schemas";
import { Page } from "./Page";
import { Field, FIELD } from "./Screener";
import { Card, Empty, ErrorBox, Loading, TINT } from "./ui";

const ORDER = Object.keys(STRATEGY_LABEL);

function Pct({ v, digits = 1, sign = true }: { v: number | null; digits?: number; sign?: boolean }) {
  return <span className={sign ? tone(v) : undefined}>{fmtPct(v, digits, sign)}</span>;
}

const rules = (r: BacktestRun): string =>
  [r.entry, r.regime === "none" ? "no regime gate" : r.regime && `${r.regime} regime`, r.rule]
    .filter((x): x is string => !!x)
    .join(" · ") || DASH;

/** The stored backtest runs of the research database: read only, nothing is run here. */
export function Backtest() {
  const bt = useBacktests();
  const [strategy, setStrategy] = useState("");
  const [period, setPeriod] = useState("");
  const [paperOnly, setPaperOnly] = useState(true);

  const all = useMemo(() => bt.data?.runs ?? [], [bt.data]);
  const rows = useMemo(
    () =>
      all
        .filter((r) => (!strategy || r.strategy_id === strategy) && (!period || r.period === period) && (!paperOnly || r.paper_rules))
        .sort(
          (a, b) =>
            ORDER.indexOf(a.strategy_id) - ORDER.indexOf(b.strategy_id) ||
            (a.period ?? "").localeCompare(b.period ?? "") ||
            (b.completed_at ?? "").localeCompare(a.completed_at ?? ""),
        ),
    [all, strategy, period, paperOnly],
  );
  const periods = useMemo(() => [...new Set(all.map((r) => r.period).filter((p): p is string => !!p))].sort(), [all]);

  return (
    <Page active="Backtest" title="Backtest">
      <Card title="Filters" label="Filters">
        <div className="flex flex-wrap items-end gap-3">
          <Field id="bt-strategy" label="Strategy">
            <select id="bt-strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)} className={FIELD}>
              <option value="">All strategies</option>
              {ORDER.map((id) => (
                <option key={id} value={id}>
                  {strategyLabel(id)}
                </option>
              ))}
            </select>
          </Field>
          <Field id="bt-period" label="Period">
            <select id="bt-period" value={period} onChange={(e) => setPeriod(e.target.value)} className={FIELD}>
              <option value="">All periods</option>
              {periods.map((p) => (
                <option key={p} value={p}>
                  {sentence(p)}
                </option>
              ))}
            </select>
          </Field>
          <label
            className="flex cursor-pointer items-center gap-1.5 pb-1.5 text-xs text-ink"
            title="Entry, regime, exit rule, 10 positions and 15 bps costs of the frozen paper rules"
          >
            <input type="checkbox" checked={paperOnly} onChange={(e) => setPaperOnly(e.target.checked)} />
            Frozen paper rules only
          </label>
        </div>
      </Card>

      <Card
        title="Stored runs"
        subtitle="Portfolio results of runs stored by `vcp backtest run`. Display only; nothing is recalculated here."
        box={TINT.violet}
        right={bt.data?.available ? <span className="text-xs text-mute">{`${fmtInt(rows.length)} of ${fmtInt(all.length)} runs`}</span> : undefined}
      >
        {bt.isError ? (
          <ErrorBox error={bt.error} />
        ) : bt.isPending ? (
          <Loading what="backtest runs" />
        ) : !bt.data.available ? (
          <Empty>{bt.data.reason ?? "No backtest runs are available."}</Empty>
        ) : rows.length === 0 ? (
          <Empty>No stored run matches these filters.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1150px] text-left text-xs">
              <thead className="text-[11px] uppercase text-mute">
                <tr>
                  {["Strategy", "Period", "Rules", "Trades", "Win rate", "Avg trade", "Profit factor", "Return", "Max drawdown", "Exposure", "Run"].map((h, i) => (
                    <th key={h} scope="col" className={`px-3 py-1.5 font-medium ${i < 3 ? "text-left" : "text-center"}`}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.backtest_id} className="border-t border-line">
                    <td className="px-3 py-1.5 font-medium text-ink">{strategyLabel(r.strategy_id)}</td>
                    <td className="px-3 py-1.5">
                      <span className="text-ink">{r.period ? sentence(r.period) : DASH}</span>
                      <span className="block text-[11px] text-mute">{`${fmtDay(r.start_date)} to ${fmtDay(r.end_date)}`}</span>
                    </td>
                    <td className="px-3 py-1.5" title={`${r.cost_bps ?? DASH} bps a side · ${r.max_positions ?? DASH} positions · survivorship ${r.survivorship ?? DASH}`}>
                      {rules(r)}
                      {r.paper_rules ? <span className="ml-1 rounded bg-accent/20 px-1 text-[10px] text-accent">paper rules</span> : null}
                    </td>
                    <td className="px-3 py-1.5 text-center tabular-nums">{fmtInt(r.portfolio.trades)}</td>
                    <td className="px-3 py-1.5 text-center tabular-nums">{fmtPct(r.portfolio.win_rate_pct, 0)}</td>
                    <td className="px-3 py-1.5 text-center"><Pct v={r.portfolio.avg_return_pct} digits={2} /></td>
                    <td className="px-3 py-1.5 text-center tabular-nums">{fmtNum(r.portfolio.profit_factor, 2)}</td>
                    <td className="px-3 py-1.5 text-center"><Pct v={r.portfolio.total_return_pct} /></td>
                    <td className="px-3 py-1.5 text-center"><Pct v={r.portfolio.max_drawdown_pct} /></td>
                    <td className="px-3 py-1.5 text-center tabular-nums">{fmtPct(r.portfolio.avg_exposure_pct, 0)}</td>
                    <td className="px-3 py-1.5 text-center text-mute">{fmtDay(r.completed_at?.slice(0, 10))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="How to read these" box={TINT.blue}>
        <ul className="list-disc space-y-1 pl-5 text-xs text-mute">
          <li>Development is the period the rules were chosen on; validation comes after it. Judge a strategy by validation, not by development.</li>
          <li>Survivorship is partial: stocks that were delisted may be missing, so results can look better than real trading.</li>
          <li>A value a run did not store shows {DASH}, never 0. The paper ledger, not a backtest, decides the review on or after 2027-04-01.</li>
        </ul>
      </Card>
    </Page>
  );
}
