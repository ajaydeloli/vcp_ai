"use client";

import { useQueries } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { getJson, paths, useOverlap, useSetups } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, strategyLabel } from "@/lib/fmt";
import * as schemas from "@/lib/schemas";
import type { SetupRow, Summary } from "@/lib/schemas";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";

export type Selection = { symbol: string; strategy: string };

type Props = {
  strategies: { strategy_id: string }[];
  summary?: Summary;
  selected: Selection | null;
  onSelect: (s: Selection) => void;
};

type Col = {
  key: string;
  label: string;
  align?: "right";
  get: (r: SetupRow) => number | string | null;
  render: (r: SetupRow) => React.ReactNode;
};

const COLS: Col[] = [
  {
    key: "symbol",
    label: "Symbol",
    get: (r) => r.symbol,
    render: (r) => (
      <div>
        <span className="font-medium text-ink">{r.symbol}</span>
        {r.company ? <div className="max-w-36 truncate text-[11px] text-mute">{r.company}</div> : null}
      </div>
    ),
  },
  { key: "score", label: "Score", align: "right", get: (r) => r.score, render: (r) => fmtNum(r.score) },
  {
    key: "grade",
    label: "Grade",
    get: (r) => r.grade,
    render: (r) => <GradeBadge classification={r.classification} grade={r.grade} />,
  },
  { key: "status", label: "Status", get: (r) => r.status, render: (r) => <StatusPill status={r.status} /> },
  { key: "rs", label: "RS", align: "right", get: (r) => r.rs_rank, render: (r) => fmtInt(r.rs_rank) },
  { key: "pivot", label: "Pivot", align: "right", get: (r) => r.pivot, render: (r) => fmtPrice(r.pivot) },
  {
    key: "dist",
    label: "To pivot",
    align: "right",
    get: (r) => r.pivot_distance_pct,
    render: (r) => fmtPct(r.pivot_distance_pct),
  },
  { key: "stop", label: "Stop", align: "right", get: (r) => r.stop, render: (r) => fmtPrice(r.stop) },
  {
    key: "base",
    label: "Base days",
    align: "right",
    get: (r) => r.base_days,
    render: (r) => fmtInt(r.base_days),
  },
  {
    key: "brk",
    label: "Breakout",
    get: (r) => r.breakout_date,
    render: (r) => (r.breakout_date ? fmtDay(r.breakout_date) : DASH),
  },
];

const STRATEGY_COL: Col = {
  key: "strategy",
  label: "Strategy",
  get: (r) => r.strategy_id,
  render: (r) => <span className="text-mute">{strategyLabel(r.strategy_id)}</span>,
};

export function sortRows(rows: SetupRow[], col: Col, dir: "asc" | "desc"): SetupRow[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = col.get(a);
    const y = col.get(b);
    if (x === null && y === null) return 0;
    if (x === null) return 1; // a missing value is last whichever way we sort
    if (y === null) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * sign;
  });
}

const BREAKOUTS = "breakouts";
const OVERLAP = "overlap";

export function SetupsTable({ strategies, summary, selected, onSelect }: Props) {
  const [tab, setTab] = useState<string>(strategies[0]?.strategy_id ?? "vcp");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "score", dir: "desc" });

  const isStrategy = tab !== BREAKOUTS && tab !== OVERLAP;
  const single = useSetups(tab, undefined, isStrategy);
  const overlap = useOverlap(tab === OVERLAP);
  const breakouts = useQueries({
    queries: strategies.map((s) => ({
      queryKey: ["setups", s.strategy_id, "BREAKOUT"],
      queryFn: () => getJson(paths.setups(s.strategy_id, "BREAKOUT"), schemas.setups),
      enabled: tab === BREAKOUTS,
      staleTime: 60_000,
    })),
  });

  const columns = tab === BREAKOUTS ? [COLS[0] as Col, STRATEGY_COL, ...COLS.slice(1)] : COLS;
  const col = columns.find((c) => c.key === sort.key) ?? (COLS[1] as Col);

  const rows: SetupRow[] = useMemo(() => {
    const raw =
      tab === BREAKOUTS
        ? breakouts.flatMap((q) => q.data?.rows ?? [])
        : isStrategy
          ? (single.data?.rows ?? [])
          : [];
    return sortRows(raw, col, sort.dir);
  }, [tab, isStrategy, breakouts, single.data, col, sort.dir]);

  const count = (id: string) => summary?.strategies.find((s) => s.strategy_id === id)?.ranked;
  const tabs = [
    ...strategies.map((s) => ({ id: s.strategy_id, label: strategyLabel(s.strategy_id), n: count(s.strategy_id) })),
    { id: BREAKOUTS, label: "Breakouts", n: undefined },
    { id: OVERLAP, label: "On several lists", n: overlap.data?.rows.length },
  ];

  const loading =
    tab === OVERLAP ? overlap.isPending : tab === BREAKOUTS ? breakouts.some((q) => q.isPending) : single.isPending;
  const error =
    tab === OVERLAP ? overlap.error : tab === BREAKOUTS ? breakouts.find((q) => q.error)?.error : single.error;
  const asOf = tab === OVERLAP ? overlap.data?.as_of : single.data?.as_of;

  return (
    <Card
      title="Setups: watch list"
      subtitle={`Ranked by setup score${asOf ? `, scan of ${fmtDay(asOf)}` : ""}. A watch list for research, not buy signals.`}
      className="min-w-0 xl:absolute xl:inset-0"
    >
      <div role="tablist" aria-label="Setup lists" className="mb-3 flex flex-wrap gap-1 border-b border-line pb-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded px-2.5 py-1 text-xs ${
              tab === t.id ? "bg-accent text-white" : "text-mute hover:text-ink"
            }`}
          >
            {t.label}
            {t.n !== undefined ? <span className="ml-1 opacity-70">{t.n}</span> : null}
          </button>
        ))}
      </div>

      {error ? (
        <ErrorBox error={error} />
      ) : loading ? (
        <Loading what="setups" />
      ) : tab === OVERLAP ? (
        <OverlapList rows={overlap.data?.rows ?? []} onSelect={onSelect} selected={selected} />
      ) : rows.length === 0 ? (
        <Empty>No setups in this list on this scan date.</Empty>
      ) : (
        <div className="min-h-0 max-h-[520px] flex-1 overflow-auto xl:max-h-none">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-panel text-[11px] uppercase text-mute">
              <tr>
                <th className="px-2 py-1.5 font-medium">#</th>
                {columns.map((c) => (
                  <th
                    key={c.key}
                    className={`px-2 py-1.5 font-medium ${c.align === "right" ? "text-right" : ""}`}
                    aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                  >
                    <button
                      type="button"
                      onClick={() =>
                        setSort((s) => ({
                          key: c.key,
                          dir: s.key === c.key && s.dir === "desc" ? "asc" : "desc",
                        }))
                      }
                      className="uppercase hover:text-ink"
                    >
                      {c.label}
                      {sort.key === c.key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const on = selected?.symbol === r.symbol && selected.strategy === r.strategy_id;
                return (
                  <tr
                    key={`${r.strategy_id}:${r.symbol}`}
                    aria-selected={on}
                    onClick={() => onSelect({ symbol: r.symbol, strategy: r.strategy_id })}
                    className={`cursor-pointer border-t border-line/60 hover:bg-panel2 ${on ? "bg-panel2" : ""}`}
                  >
                    <td className="px-2 py-1.5 text-mute">{i + 1}</td>
                    {columns.map((c) => (
                      <td key={c.key} className={`px-2 py-1.5 ${c.align === "right" ? "text-right tabular-nums" : ""}`}>
                        {c.render(r)}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function OverlapList({
  rows,
  selected,
  onSelect,
}: {
  rows: schemas.Overlap["rows"];
  selected: Selection | null;
  onSelect: (s: Selection) => void;
}) {
  if (rows.length === 0) return <Empty>No stock is on more than one list on this scan date.</Empty>;
  return (
    <ul className="min-h-0 max-h-[520px] flex-1 divide-y xl:max-h-none divide-line/60 overflow-auto text-xs">
      {rows.map((r) => (
        <li key={r.symbol}>
          <button
            type="button"
            onClick={() => onSelect({ symbol: r.symbol, strategy: r.strategies[0]?.strategy_id ?? "vcp" })}
            className={`flex w-full items-center justify-between gap-3 px-2 py-2 text-left hover:bg-panel2 ${
              selected?.symbol === r.symbol ? "bg-panel2" : ""
            }`}
          >
            <span>
              <span className="font-medium text-ink">{r.symbol}</span>
              {r.company ? <span className="ml-2 text-mute">{r.company}</span> : null}
            </span>
            <span className="flex flex-wrap justify-end gap-1">
              {r.strategies.map((s) => (
                <span key={s.strategy_id} className="rounded border border-line bg-panel2 px-1.5 py-0.5 text-[11px]">
                  {strategyLabel(s.strategy_id)} · {fmtNum(s.score)}
                </span>
              ))}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
