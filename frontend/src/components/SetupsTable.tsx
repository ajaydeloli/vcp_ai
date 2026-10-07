"use client";

import { useMemo, useState } from "react";
import { useOverlap, useSetups } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtNum, fmtPct, fmtPrice, strategyLabel, tone } from "@/lib/fmt";
import { countList, PAGE_SIZE, pageNumbers, VCP_LISTS } from "@/lib/lists";
import type * as schemas from "@/lib/schemas";
import type { SetupRow } from "@/lib/schemas";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";

export type Selection = { symbol: string; strategy: string };

type Props = {
  strategies: { strategy_id: string }[];
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

const scoreTone = (v: number | null): string =>
  v === null ? "text-mute" : v >= 85 ? "bg-up/20 text-up" : v >= 70 ? "bg-accent/20 text-accent" : "bg-panel2 text-ink";

const COLS: Col[] = [
  {
    key: "symbol",
    label: "Symbol",
    get: (r) => r.symbol,
    render: (r) => <span className="font-medium text-accent">{r.symbol}</span>,
  },
  {
    key: "company",
    label: "Company",
    get: (r) => r.company,
    render: (r) => <span className="block max-w-40 truncate text-ink">{r.company ?? DASH}</span>,
  },
  {
    key: "grade",
    label: "Setup",
    get: (r) => r.grade,
    render: (r) => <GradeBadge classification={r.classification} grade={r.grade} />,
  },
  {
    key: "score",
    label: "Score",
    align: "right",
    get: (r) => r.score,
    render: (r) => (
      <span className={`inline-block min-w-9 rounded px-1.5 py-0.5 text-center font-medium ${scoreTone(r.score)}`}>
        {fmtNum(r.score, 0)}
      </span>
    ),
  },
  { key: "rs", label: "RS", align: "right", get: (r) => r.rs_rank, render: (r) => fmtInt(r.rs_rank) },
  { key: "pivot", label: "Pivot", align: "right", get: (r) => r.pivot, render: (r) => fmtPrice(r.pivot) },
  { key: "price", label: "Price", align: "right", get: (r) => r.close, render: (r) => fmtPrice(r.close) },
  { key: "status", label: "Status", get: (r) => r.status, render: (r) => <StatusPill status={r.status} /> },
  {
    key: "change",
    label: "Change",
    align: "right",
    get: (r) => r.change_pct,
    render: (r) => <span className={tone(r.change_pct)}>{fmtPct(r.change_pct, 1, true)}</span>,
  },
];

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

const OVERLAP = "overlap";
const LIST = "list:";

export function Pager({
  page,
  total,
  onPage,
}: {
  page: number;
  total: number;
  onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);
  const btn = "min-w-7 rounded border px-2 py-1 text-xs";
  return (
    <nav aria-label="Pages" className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-mute">
      <span>
        Showing {from}–{to} of {total}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="Previous page"
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
          className={`${btn} border-line enabled:hover:text-ink disabled:opacity-40`}
        >
          ‹
        </button>
        {pageNumbers(page, pages).map((p, i) =>
          p === "…" ? (
            <span key={`gap${i}`} className="px-1">
              …
            </span>
          ) : (
            <button
              key={p}
              type="button"
              aria-label={`Page ${p + 1}`}
              aria-current={p === page ? "page" : undefined}
              onClick={() => onPage(p)}
              className={`${btn} ${p === page ? "border-accent bg-accent text-white" : "border-line hover:text-ink"}`}
            >
              {p + 1}
            </button>
          ),
        )}
        <button
          type="button"
          aria-label="Next page"
          disabled={page >= pages - 1}
          onClick={() => onPage(page + 1)}
          className={`${btn} border-line enabled:hover:text-ink disabled:opacity-40`}
        >
          ›
        </button>
      </div>
    </nav>
  );
}

export function SetupsTable({ strategies, selected, onSelect }: Props) {
  const [tab, setTab] = useState<string>(`${LIST}top`);
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "score", dir: "desc" });

  const vcpList = tab.startsWith(LIST);
  const strategy = vcpList ? "vcp" : tab;
  const isStrategy = tab !== OVERLAP;
  const single = useSetups(strategy, undefined, isStrategy);
  const vcp = useSetups("vcp");
  const overlap = useOverlap(tab === OVERLAP);

  const col = COLS.find((c) => c.key === sort.key) ?? (COLS[3] as Col);

  const rows: SetupRow[] = useMemo(() => {
    const raw = single.data?.rows ?? [];
    const keep = vcpList ? (VCP_LISTS.find((l) => `${LIST}${l.id}` === tab)?.test ?? (() => true)) : () => true;
    return sortRows(raw.filter(keep), col, sort.dir);
  }, [single.data, vcpList, tab, col, sort.dir]);

  const others = strategies.filter((s) => s.strategy_id !== "vcp");
  const tabs = [
    ...VCP_LISTS.map((l) => ({
      id: `${LIST}${l.id}`,
      label: l.label,
      n: vcp.data ? countList(vcp.data.rows, l.id) : undefined,
    })),
    ...others.map((s) => ({ id: s.strategy_id, label: strategyLabel(s.strategy_id), n: undefined })),
    { id: OVERLAP, label: "On several lists", n: overlap.data?.rows.length },
  ];

  const loading = tab === OVERLAP ? overlap.isPending : single.isPending;
  const error = tab === OVERLAP ? overlap.error : single.error;
  const asOf = tab === OVERLAP ? overlap.data?.as_of : single.data?.as_of;

  const total = tab === OVERLAP ? (overlap.data?.rows.length ?? 0) : rows.length;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  const at = Math.min(page, lastPage);
  const slice = rows.slice(at * PAGE_SIZE, (at + 1) * PAGE_SIZE);

  return (
    <Card
      title="Watch list"
      subtitle={`Ranked by setup score${asOf ? `, scan of ${fmtDay(asOf)}` : ""}. A watch list for research, not buy signals.`}
      className="min-w-0"
    >
      <div className="mb-3 flex flex-wrap items-center gap-3 border-b border-line pb-3">
        <label htmlFor="setup-list" className="text-xs text-mute">
          List
        </label>
        <select
          id="setup-list"
          aria-label="Setup list"
          value={tab}
          onChange={(e) => {
            setTab(e.target.value);
            setPage(0);
          }}
          className="min-w-52 rounded-lg border border-line bg-panel2 px-3 py-1.5 text-sm text-ink focus:border-accent focus:outline-none"
        >
          <optgroup label="VCP lists">
            {tabs
              .filter((t) => t.id.startsWith(LIST))
              .map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                  {t.n !== undefined ? ` (${t.n})` : ""}
                </option>
              ))}
          </optgroup>
          <optgroup label="Other strategies">
            {tabs
              .filter((t) => !t.id.startsWith(LIST) && t.id !== OVERLAP)
              .map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
          </optgroup>
          <optgroup label="Across strategies">
            {tabs
              .filter((t) => t.id === OVERLAP)
              .map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                  {t.n !== undefined ? ` (${t.n})` : ""}
                </option>
              ))}
          </optgroup>
        </select>
      </div>

      {error ? (
        <ErrorBox error={error} />
      ) : loading ? (
        <Loading what="setups" />
      ) : tab === OVERLAP ? (
        <>
          <OverlapList
            rows={(overlap.data?.rows ?? []).slice(at * PAGE_SIZE, (at + 1) * PAGE_SIZE)}
            onSelect={onSelect}
            selected={selected}
          />
          <Pager page={at} total={total} onPage={setPage} />
        </>
      ) : rows.length === 0 ? (
        <Empty>No setups in this list on this scan date.</Empty>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[11px] uppercase text-mute">
                <tr>
                  <th className="px-2 py-1.5 font-medium">#</th>
                  {COLS.map((c) => (
                    <th
                      key={c.key}
                      className={`px-2 py-1.5 font-medium ${c.align === "right" ? "text-right" : ""}`}
                      aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                    >
                      <button
                        type="button"
                        onClick={() => {
                          setSort((s) => ({ key: c.key, dir: s.key === c.key && s.dir === "desc" ? "asc" : "desc" }));
                          setPage(0);
                        }}
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
                {slice.map((r, i) => {
                  const on = selected?.symbol === r.symbol && selected.strategy === r.strategy_id;
                  return (
                    <tr
                      key={`${r.strategy_id}:${r.symbol}`}
                      aria-selected={on}
                      onClick={() => onSelect({ symbol: r.symbol, strategy: r.strategy_id })}
                      className={`cursor-pointer border-t border-line/60 hover:bg-panel2 ${on ? "bg-panel2" : ""}`}
                    >
                      <td className="px-2 py-2 text-mute">{at * PAGE_SIZE + i + 1}</td>
                      {COLS.map((c) => (
                        <td key={c.key} className={`px-2 py-2 ${c.align === "right" ? "text-right tabular-nums" : ""}`}>
                          {c.render(r)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pager page={at} total={total} onPage={setPage} />
        </>
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
    <ul className="divide-y divide-line/60 text-xs">
      {rows.map((r) => (
        <li key={r.symbol}>
          <button
            type="button"
            onClick={() => onSelect({ symbol: r.symbol, strategy: r.strategies[0]?.strategy_id ?? "vcp" })}
            className={`flex w-full items-center justify-between gap-3 px-2 py-2.5 text-left hover:bg-panel2 ${
              selected?.symbol === r.symbol ? "bg-panel2" : ""
            }`}
          >
            <span>
              <span className="font-medium text-accent">{r.symbol}</span>
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
