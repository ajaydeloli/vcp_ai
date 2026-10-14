"use client";

// Shareholding pattern on the stock page (F8; FUNDAMENTALS_SPECIFICATION §15): the compact block
// of the Fundamentals card and the quarterly card of Stock Analysis. Display only: nothing here
// changes a setup score or the paper ledger.
import { useStockFundamentals } from "@/lib/api";
import { DASH, fmtDay, fmtInt, fmtPct, tone } from "@/lib/fmt";
import { fmtPp, quarterLabel } from "@/lib/fundamentals";
import type { ShareholdingQuarter, ShareholdingSummary } from "@/lib/schemas";
import { Card, Empty, ErrorBox, Loading, TINT } from "./ui";

export const NO_SHAREHOLDING = "No shareholding pattern filed yet";
const PLEDGE_NOTE =
  "Pledge is % of the promoter group's own shares. From the June 2025 quarter it counts pledges only (before: pledged or otherwise encumbered), so no change is shown across that switch.";

/** The parts of 100 % in the bars, in order. Every part is also a table column: colour is never the only cue. */
const PARTS = [
  { key: "promoter", label: "Promoter", cls: "fill-[#f5a524]", dot: "bg-[#f5a524]", get: (q: ShareholdingQuarter) => q.promoter_pct },
  { key: "fii", label: "FII", cls: "fill-[#4aa3ff]", dot: "bg-[#4aa3ff]", get: (q: ShareholdingQuarter) => q.fii_pct },
  { key: "dii", label: "DII", cls: "fill-[#26c281]", dot: "bg-[#26c281]", get: (q: ShareholdingQuarter) => q.dii_pct },
  {
    key: "retail",
    label: "Retail & others",
    cls: "fill-[#8b5cf6]",
    dot: "bg-[#8b5cf6]",
    get: (q: ShareholdingQuarter) => Math.max(0, q.public_pct - q.fii_pct - q.dii_pct),
  },
  { key: "other", label: "Trusts", cls: "fill-[#94a3b8]", dot: "bg-[#94a3b8]", get: (q: ShareholdingQuarter) => q.other_pct },
] as const;

function Change({ value, invert = false }: { value: number | null; invert?: boolean }) {
  if (value === null) {
    return (
      <span className="text-mute" title="The quarter to compare with was not filed" aria-label="Not available">
        {DASH}
      </span>
    );
  }
  return <span className={tone(invert ? -value : value)}>{fmtPp(value, 2)}</span>;
}

export function PledgeFlag({ s }: { s: Pick<ShareholdingSummary, "pledge_rising"> }) {
  return s.pledge_rising ? (
    <span className="ml-1.5 rounded border border-down/60 px-1 text-[10px] text-down" title="The promoters pledged more of their shares than a quarter before">
      ▲ rising
    </span>
  ) : null;
}

/** The latest split with its change over one and four quarters (the Fundamentals card). */
export function ShareholdingBlock({ s }: { s: ShareholdingSummary | null | undefined }) {
  if (!s) return <p className="mt-2 border-t border-line pt-2 text-[11px] text-mute">Shareholding: {NO_SHAREHOLDING.toLowerCase()}</p>;
  const rows: { k: string; v: number; c1: number | null; c4: number | null }[] = [
    { k: "Promoter", v: s.promoter_pct, c1: s.promoter_change_1q, c4: s.promoter_change_4q },
    { k: "FII", v: s.fii_pct, c1: s.fii_change_1q, c4: s.fii_change_4q },
    { k: "DII", v: s.dii_pct, c1: s.dii_change_1q, c4: s.dii_change_4q },
  ];
  return (
    <div className="mt-2 border-t border-line pt-2">
      <p className="mb-1 flex justify-between gap-2 text-[11px] text-mute">
        <span>
          Shareholding · {quarterLabel(s.as_on)}
          {s.revision > 0 ? " (revised)" : ""}
        </span>
        {s.stale ? (
          <span className="text-warn" title={`The latest pattern is as on ${fmtDay(s.as_on)}; the next one is overdue`}>
            ⚠ Stale
          </span>
        ) : null}
      </p>
      <table className="w-full text-right text-xs tabular-nums">
        <thead className="text-[10px] uppercase text-mute">
          <tr>
            <th scope="col" className="py-0.5 text-left font-medium">
              Holder
            </th>
            <th scope="col" className="py-0.5 font-medium">
              Holding
            </th>
            <th scope="col" className="py-0.5 font-medium" title="Change over one quarter, percentage points">
              1Q
            </th>
            <th scope="col" className="py-0.5 font-medium" title="Change over four quarters, percentage points">
              4Q
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.k}>
              <th scope="row" className="py-0.5 text-left font-normal text-mute">
                {r.k}
              </th>
              <td className="py-0.5 text-ink">{fmtPct(r.v, 2)}</td>
              <td className="py-0.5">
                <Change value={r.c1} />
              </td>
              <td className="py-0.5">
                <Change value={r.c4} />
              </td>
            </tr>
          ))}
          <tr>
            <th scope="row" className="py-0.5 text-left font-normal text-mute" title={PLEDGE_NOTE}>
              Pledged
            </th>
            <td className="py-0.5 text-ink">
              {fmtPct(s.promoter_pledged_pct, 2)}
              <PledgeFlag s={s} />
            </td>
            <td className="py-0.5">
              <Change value={s.pledged_change_1q} invert />
            </td>
            <td className="py-0.5 text-mute">{DASH}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/** One 100 % column per quarter end, oldest on the left. */
export function ShareholdingBars({ quarters, height = 150 }: { quarters: ShareholdingQuarter[]; height?: number }) {
  if (quarters.length === 0) return null;
  const slot = 40;
  const bar = 22;
  const top = 4;
  const bottom = 16;
  const width = quarters.length * slot;
  const plot = height - top - bottom;
  const last = quarters[quarters.length - 1] as ShareholdingQuarter;
  return (
    <svg
      role="img"
      aria-label={`Shareholding by quarter, ${quarters.length} quarters; latest promoter ${fmtPct(last.promoter_pct, 1)}, FII ${fmtPct(last.fii_pct, 1)}, DII ${fmtPct(last.dii_pct, 1)}`}
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      style={{ maxHeight: height }}
    >
      {quarters.map((q, i) => {
        const x = i * slot + (slot - bar) / 2;
        let y = top;
        return (
          <g key={q.as_on}>
            {PARTS.map((p) => {
              const h = (Math.max(0, p.get(q)) / 100) * plot;
              const rect = (
                <rect key={p.key} x={x} y={y} width={bar} height={h} className={p.cls}>
                  <title>{`${quarterLabel(q.as_on)} · ${p.label} ${fmtPct(p.get(q), 2)}`}</title>
                </rect>
              );
              y += h;
              return rect;
            })}
            <text x={i * slot + slot / 2} y={height - 3} textAnchor="middle" className="fill-mute text-[9px]">
              {quarterLabel(q.as_on)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function Legend() {
  return (
    <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-mute">
      {PARTS.map((p) => (
        <li key={p.key} className="flex items-center gap-1">
          <span className={`inline-block h-2 w-2 rounded-sm ${p.dot}`} aria-hidden />
          {p.label}
        </li>
      ))}
    </ul>
  );
}

/** Up to eight quarter ends, as filed (latest revision broadcast by the day shown). */
export function ShareholdingCard({ symbol }: { symbol: string }) {
  const res = useStockFundamentals(symbol);
  const quarters = res.data?.shareholding_quarters ?? [];
  const s = res.data?.shareholding ?? null;
  return (
    <Card
      title="Shareholding pattern"
      box={TINT.violet}
      subtitle="Quarter-end filings to NSE, % of the company's shares. FII: foreign portfolio investors; DII: mutual funds, insurers, banks and other domestic institutions."
      right={s ? <PledgeFlag s={s} /> : undefined}
    >
      {res.isError ? (
        <ErrorBox error={res.error} />
      ) : res.isPending ? (
        <Loading what="shareholding" />
      ) : quarters.length === 0 ? (
        <Empty>{NO_SHAREHOLDING}</Empty>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <figure className="min-w-0">
            <figcaption className="mb-1 text-[11px] uppercase text-mute">Holders by quarter</figcaption>
            <ShareholdingBars quarters={quarters} />
            <Legend />
          </figure>
          <div className="min-w-0 overflow-x-auto">
            <table className="w-full min-w-[620px] text-right text-xs tabular-nums">
              <thead className="text-[11px] uppercase text-mute">
                <tr>
                  <th scope="col" className="px-2 py-1 text-left font-medium">
                    Quarter
                  </th>
                  <th scope="col" className="px-2 py-1 text-left font-medium">
                    Filed
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    Promoter
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium" title={PLEDGE_NOTE}>
                    Pledged
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    FII
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    DII
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    of which MF
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    Retail & others
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    Shareholders
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...quarters].reverse().map((q) => (
                  <tr key={q.as_on} className="border-t border-line">
                    <th scope="row" className="px-2 py-1 text-left font-normal text-ink">
                      {quarterLabel(q.as_on)}
                      {q.revision > 0 ? (
                        <span className="ml-1.5 rounded border border-line px-1 text-[10px] text-mute" title="Revised filing">
                          rev.
                        </span>
                      ) : null}
                    </th>
                    <td className="px-2 py-1 text-left text-mute">{fmtDay(q.available_at.slice(0, 10))}</td>
                    <td className="px-2 py-1">{fmtPct(q.promoter_pct, 2)}</td>
                    <td className="px-2 py-1">{fmtPct(q.promoter_pledged_pct, 2)}</td>
                    <td className="px-2 py-1">{fmtPct(q.fii_pct, 2)}</td>
                    <td className="px-2 py-1">{fmtPct(q.dii_pct, 2)}</td>
                    <td className="px-2 py-1">{fmtPct(q.mf_pct, 2)}</td>
                    <td className="px-2 py-1">{fmtPct(Math.max(0, q.public_pct - q.fii_pct - q.dii_pct), 2)}</td>
                    <td className="px-2 py-1">{fmtInt(q.shareholders)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <p className="mt-3 text-[11px] text-mute">{PLEDGE_NOTE} Shown only: shareholding is not part of any setup score.</p>
    </Card>
  );
}
