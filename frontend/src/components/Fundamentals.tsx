"use client";

// Fundamentals on the stock page (F5; FUNDAMENTALS_SPECIFICATION §11): the overview card and the
// quarterly results card. Display only: nothing here changes a setup score or the paper ledger.
import { useStockFundamentals } from "@/lib/api";
import { DASH, fmtDay, fmtNum, fmtPct, tone } from "@/lib/fmt";
import { SUPPORTING_NOTE, fmtCrore, fmtPp, gateText, quarterLabel, statusText } from "@/lib/fundamentals";
import type { FundamentalQuarter, FundamentalSummary } from "@/lib/schemas";
import { Card, Empty, ErrorBox, Loading, TINT } from "./ui";

/** "2026-07-17T19:50:03+05:30" -> "17 Jul 2026" (the API stamps IST; the date part is IST). */
const publishedDay = (iso: string | null | undefined): string => (iso ? fmtDay(iso.slice(0, 10)) : DASH);

function Row({ k, v, hint }: { k: string; v: React.ReactNode; hint?: string }) {
  return (
    <div className="flex justify-between gap-3 py-1">
      <dt className="text-mute">{k}</dt>
      <dd className="min-w-0 text-right tabular-nums text-ink" title={hint}>
        {v}
      </dd>
    </div>
  );
}

/** A growth figure in its up/down colour, or a dash whose tooltip says why it is missing. */
function Growth({ value, status, pp }: { value: number | null; status?: string; pp?: boolean }) {
  if (value === null) {
    const why = statusText(status);
    return (
      <span className="text-mute" title={why} aria-label={why ? `Not available: ${why}` : "Not available"}>
        {DASH}
      </span>
    );
  }
  return <span className={tone(value)}>{pp ? fmtPp(value) : fmtPct(value, 1, true)}</span>;
}

export function Freshness({ s }: { s: FundamentalSummary }) {
  return s.stale ? (
    <span className="text-[11px] text-warn" title={`The latest quarter (${fmtDay(s.period_end)}) is older than the staleness limit`}>
      ⚠ Stale
    </span>
  ) : (
    <span className="text-[11px] text-up">● Current</span>
  );
}

/** Quarterly EPS as thin bars from a zero line (one series: no legend; the card title names it).
 *  Estimated quarters are hatched and labelled "est." in their tooltip, never told by colour alone. */
export function EpsBars({ quarters, height = 120, labels = true }: { quarters: FundamentalQuarter[]; height?: number; labels?: boolean }) {
  const pts = quarters.filter((q) => q.eps !== null);
  if (pts.length < 2) return null;
  const slot = 36;
  const bar = 14;
  const top = labels ? 14 : 4;
  const bottom = labels ? 16 : 2;
  const width = quarters.length * slot;
  const values = pts.map((q) => q.eps as number);
  const max = Math.max(0, ...values);
  const min = Math.min(0, ...values);
  const span = max - min || 1;
  const plot = height - top - bottom;
  const y = (v: number) => top + ((max - v) / span) * plot;
  const zero = y(0);
  const last = quarters[quarters.length - 1];
  return (
    <svg
      role="img"
      aria-label={`Quarterly EPS, ${quarters.length} quarters, latest ${fmtNum(last?.eps ?? null, 2)}`}
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      style={{ maxHeight: height }}
    >
      <defs>
        <pattern id="eps-est" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="4" height="4" className="fill-accent/40" />
          <line x1="0" y1="0" x2="0" y2="4" className="stroke-accent" strokeWidth="2" />
        </pattern>
      </defs>
      <line x1={0} x2={width} y1={zero} y2={zero} className="stroke-line" strokeWidth={1} />
      {quarters.map((q, i) => {
        const cx = i * slot + slot / 2;
        const tip = `${quarterLabel(q.period_end)}: EPS ${fmtNum(q.eps, 2)}${
          q.eps_yoy !== null ? ` (${fmtPct(q.eps_yoy, 1, true)} YoY)` : ""
        }${q.estimated ? ", est." : ""}`;
        if (q.eps === null) {
          return (
            <g key={q.period_end}>
              <title>{`${quarterLabel(q.period_end)}: EPS not filed`}</title>
              <rect x={i * slot} y={0} width={slot} height={height} fill="transparent" />
            </g>
          );
        }
        const yv = y(q.eps);
        const h = Math.max(1, Math.abs(zero - yv));
        const yTop = q.eps >= 0 ? zero - h : zero;
        const r = Math.min(4, h / 2);
        // rounded at the data end only, square on the zero line
        const d =
          q.eps >= 0
            ? `M${cx - bar / 2},${zero} V${yTop + r} Q${cx - bar / 2},${yTop} ${cx - bar / 2 + r},${yTop} H${cx + bar / 2 - r} Q${cx + bar / 2},${yTop} ${cx + bar / 2},${yTop + r} V${zero} Z`
            : `M${cx - bar / 2},${zero} V${zero + h - r} Q${cx - bar / 2},${zero + h} ${cx - bar / 2 + r},${zero + h} H${cx + bar / 2 - r} Q${cx + bar / 2},${zero + h} ${cx + bar / 2},${zero + h - r} V${zero} Z`;
        return (
          <g key={q.period_end} className="group">
            <title>{tip}</title>
            {/* the hit target is the whole column, wider than the bar */}
            <rect x={i * slot} y={0} width={slot} height={height} fill="transparent" />
            <path d={d} fill={q.estimated ? "url(#eps-est)" : undefined} className={q.estimated ? "" : "fill-accent group-hover:fill-accent/80"} />
            {labels ? (
              <text x={cx} y={height - 3} textAnchor="middle" className="fill-mute text-[9px]">
                {quarterLabel(q.period_end)}
              </text>
            ) : null}
            {labels && q === last ? (
              <text x={cx} y={q.eps >= 0 ? yTop - 3 : zero + h + 10} textAnchor="middle" className="fill-ink text-[10px]">
                {fmtNum(q.eps, 2)}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

/** The amber card of the stock page's overview row: the latest quarter and the seven metrics. */
export function FundamentalsCard({ symbol }: { symbol: string }) {
  const res = useStockFundamentals(symbol);
  const s = res.data?.summary ?? null;
  return (
    <Card title="Fundamentals" box={TINT.amber} right={s ? <Freshness s={s} /> : undefined}>
      {res.isError ? (
        <ErrorBox error={res.error} />
      ) : res.isPending ? (
        <Loading what="fundamentals" />
      ) : s === null ? (
        <Empty>{res.data.reason ?? "No results filed yet"}</Empty>
      ) : (
        <>
          <p className="mb-2 text-[11px] text-mute">
            {quarterLabel(s.period_end)} quarter · {s.basis.toLowerCase()} · published {publishedDay(s.available_at)}
            {s.estimated ? " · estimated" : ""}
          </p>
          <dl className="text-xs">
            <Row k="EPS (quarter / TTM)" v={`${fmtNum(s.eps, 2)} / ${fmtNum(s.ttm_eps, 2)}`} />
            <Row k="Revenue (quarter)" v={fmtCrore(s.revenue)} />
            <Row k="EPS growth YoY" v={<Growth value={s.eps_yoy} status={s.statuses["eps_yoy"]} />} />
            <Row k="EPS growth QoQ" v={<Growth value={s.eps_qoq} status={s.statuses["eps_qoq"]} />} />
            <Row k="Sales growth YoY" v={<Growth value={s.sales_yoy} status={s.statuses["sales_yoy"]} />} />
            <Row k="EPS acceleration" v={<Growth value={s.eps_acceleration} status={s.statuses["eps_acceleration"]} pp />} />
            <Row k="Operating margin" v={fmtPct(s.operating_margin)} />
            <Row k="Margin change YoY" v={<Growth value={s.margin_expansion} status={s.statuses["margin_expansion"]} pp />} />
            <Row k="ROE (TTM)" v={s.roe === null ? <Growth value={null} status={s.statuses["roe"]} /> : fmtPct(s.roe)} />
            <Row
              k="Debt / Equity"
              v={s.debt_to_equity === null ? <Growth value={null} status={s.statuses["debt_to_equity"]} /> : fmtNum(s.debt_to_equity, 2)}
            />
          </dl>
          <p className="mt-2 border-t border-line pt-2 text-[11px] text-mute">
            Data: {Math.round(s.availability * 7)} of 7 metrics
            {s.gate_pass ? "" : ` · would fail the fundamental gate (${gateText(s.gate_reason)}; shown only)`}
          </p>
        </>
      )}
    </Card>
  );
}

/** Up to twelve quarters, as published: the EPS bars and the table (the chart's table view). */
export function QuarterlyResultsCard({ symbol }: { symbol: string }) {
  const res = useStockFundamentals(symbol);
  const quarters = res.data?.quarters ?? [];
  return (
    <Card
      title="Quarterly results"
      box={TINT.amber}
      subtitle="Each quarter as published. EPS on today's share basis (splits and bonuses applied); growth against the same quarter a year earlier."
    >
      {res.isError ? (
        <ErrorBox error={res.error} />
      ) : res.isPending ? (
        <Loading what="quarterly results" />
      ) : quarters.length === 0 ? (
        <Empty>{res.data.reason ?? "No results filed yet"}</Empty>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <figure className="min-w-0">
            <figcaption className="mb-1 text-[11px] uppercase text-mute">EPS by quarter</figcaption>
            <EpsBars quarters={quarters} height={150} />
          </figure>
          <div className="min-w-0 overflow-x-auto">
            <table className="w-full min-w-[560px] text-right text-xs tabular-nums">
              <thead className="text-[11px] uppercase text-mute">
                <tr>
                  <th scope="col" className="px-2 py-1 text-left font-medium">Quarter</th>
                  <th scope="col" className="px-2 py-1 text-left font-medium">Published</th>
                  <th scope="col" className="px-2 py-1 font-medium">Revenue</th>
                  <th scope="col" className="px-2 py-1 font-medium">Sales YoY</th>
                  <th scope="col" className="px-2 py-1 font-medium">EPS</th>
                  <th scope="col" className="px-2 py-1 font-medium">EPS YoY</th>
                  <th scope="col" className="px-2 py-1 font-medium">Op. margin</th>
                  <th scope="col" className="px-2 py-1 font-medium">Net margin</th>
                </tr>
              </thead>
              <tbody>
                {[...quarters].reverse().map((q) => (
                  <tr key={q.period_end} className="border-t border-line">
                    <th scope="row" className="px-2 py-1 text-left font-normal text-ink">
                      {quarterLabel(q.period_end)}
                      {q.estimated ? (
                        <span
                          className="ml-1.5 rounded border border-line px-1 text-[10px] text-mute"
                          title="Derived from year-to-date figures (the filing had no quarter column)"
                        >
                          est.
                        </span>
                      ) : null}
                      {q.revision > 0 ? (
                        <span className="ml-1.5 rounded border border-line px-1 text-[10px] text-mute" title="Restated filing">
                          rev.
                        </span>
                      ) : null}
                    </th>
                    <td className="px-2 py-1 text-left text-mute">{publishedDay(q.available_at)}</td>
                    <td className="px-2 py-1">{fmtCrore(q.revenue)}</td>
                    <td className="px-2 py-1">
                      <Growth value={q.sales_yoy} status={q.sales_yoy_status} />
                    </td>
                    <td
                      className="px-2 py-1"
                      title={q.eps_as_filed !== null && q.eps_as_filed !== q.eps ? `As filed: ${fmtNum(q.eps_as_filed, 2)}` : undefined}
                    >
                      {fmtNum(q.eps, 2)}
                    </td>
                    <td className="px-2 py-1">
                      <Growth value={q.eps_yoy} status={q.eps_yoy_status} />
                    </td>
                    <td className="px-2 py-1">{fmtPct(q.operating_margin)}</td>
                    <td className="px-2 py-1">{fmtPct(q.net_margin)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <p className="mt-3 text-[11px] text-mute">{SUPPORTING_NOTE}</p>
    </Card>
  );
}
