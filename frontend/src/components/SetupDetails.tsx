import { DASH, fmtDay, fmtNum, fmtPct, fmtPrice, sentence } from "@/lib/fmt";
import type { StockSetup, StockSetups } from "@/lib/schemas";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-0.5">
      <dt className="text-mute">{k}</dt>
      <dd className="min-w-0 break-words text-right tabular-nums text-ink">{v}</dd>
    </div>
  );
}

const asNumber = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The rules a setup did not meet for each higher tier (stored as {tier: [rule, ...]}). */
export function unmetByTier(details: Record<string, unknown>): [string, string[]][] {
  const u = details["unmet_rules"];
  if (!u || typeof u !== "object" || Array.isArray(u)) return [];
  return Object.entries(u as Record<string, unknown>)
    .filter(([, rules]) => Array.isArray(rules) && rules.length > 0)
    .map(([tier, rules]) => [tier, (rules as unknown[]).map(String)]);
}

function ScoreParts({ setup }: { setup: StockSetup }) {
  const groups = new Map<string, StockSetup["score_parts"]>();
  for (const p of setup.score_parts) groups.set(p.component, [...(groups.get(p.component) ?? []), p]);
  if (groups.size === 0) return <p className="text-mute">No score parts stored.</p>;
  return (
    <div className="space-y-2">
      {[...groups.entries()].map(([component, parts]) => {
        const got = parts.reduce((a, p) => a + (p.points ?? 0), 0);
        const max = parts.reduce((a, p) => a + (p.max_points ?? 0), 0);
        return (
          <div key={component}>
            <div className="flex justify-between font-medium text-ink">
              <span>{sentence(component)}</span>
              <span className="tabular-nums">
                {fmtNum(got)} / {fmtNum(max, 0)}
              </span>
            </div>
            {parts.map((p) => (
              <div key={p.sub_component} className="flex justify-between pl-3 text-mute">
                <span>{sentence(p.sub_component)}</span>
                <span className="tabular-nums">
                  {fmtNum(p.points)} / {fmtNum(p.max_points, 0)}
                </span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export function SetupDetails({ stock, setup }: { stock: StockSetups; setup: StockSetup | null }) {
  const passed = stock.conditions.filter((c) => c.passed === true).length;
  const unmet = setup ? unmetByTier(setup.details) : [];
  const d = setup?.details ?? {};
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(250px,1fr))] gap-4 text-xs">
      <div>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-mute">Setup</h3>
        {setup ? (
          <>
            <dl>
              <Row k="Score" v={fmtNum(setup.score)} />
              <Row k="Pivot" v={fmtPrice(setup.pivot)} />
              <Row k="Stop" v={fmtPrice(setup.stop)} />
              <Row k="Base start" v={fmtDay(setup.base_start)} />
              <Row k="Base depth" v={fmtPct(setup.base_depth_pct)} />
              <Row k="Base high / low" v={`${fmtPrice(asNumber(d["base_high"]))} / ${fmtPrice(asNumber(d["base_low"]))}`} />
              <Row k="Prior advance" v={fmtPct(asNumber(d["prior_advance_pct"]))} />
              <Row k="Volume dry-up" v={fmtNum(asNumber(d["dryup_volume_ratio"]), 2)} />
              <Row k="Breakout" v={setup.breakout_date ? fmtDay(setup.breakout_date) : DASH} />
            </dl>
            {unmet.map(([tier, rules]) => (
              <p key={tier} className="mt-2 text-warn">
                Not {sentence(tier)}: {rules.map(sentence).join(", ")}
              </p>
            ))}
            <h3 className="mb-2 mt-4 text-[11px] font-semibold uppercase tracking-wide text-mute">
              Score breakdown
            </h3>
            <ScoreParts setup={setup} />
          </>
        ) : (
          <p className="text-mute">No setup stored for this stock on {fmtDay(stock.as_of)}.</p>
        )}
      </div>

      <div>
        <h3 className="mb-2 flex justify-between text-[11px] font-semibold uppercase tracking-wide text-mute">
          <span>Trend Template</span>
          <span className="normal-case">
            {stock.conditions.length ? `${passed} / ${stock.conditions.length}` : DASH}
          </span>
        </h3>
        {stock.conditions.length === 0 ? (
          <p className="text-mute">Not scanned on {fmtDay(stock.as_of)}.</p>
        ) : (
          <ul>
            {stock.conditions.map((c) => (
              <li key={c.name} className="flex items-center justify-between gap-2 py-0.5">
                <span className="flex items-center gap-1.5">
                  <span
                    aria-label={c.passed === null ? "not judged" : c.passed ? "passed" : "failed"}
                    className={c.passed === null ? "text-mute" : c.passed ? "text-up" : "text-down"}
                  >
                    {c.passed === null ? "•" : c.passed ? "✓" : "✗"}
                  </span>
                  <span className="text-ink">{sentence(c.name)}</span>
                </span>
                <span className="tabular-nums text-mute">
                  {fmtNum(c.measurement, 2)} vs {fmtNum(c.threshold, 2)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-mute">Weekly stage</h3>
        <dl>
          <Row k="Stage" v={stock.weekly_stage ? sentence(stock.weekly_stage) : DASH} />
          <Row
            k="Stage 2 check"
            v={stock.weekly_stage2_pass === null ? DASH : stock.weekly_stage2_pass ? "Pass" : "Fail"}
          />
          <Row
            k="Trend Template"
            v={stock.trend_template_pass === null ? DASH : stock.trend_template_pass ? "Pass" : "Fail"}
          />
        </dl>
      </div>
    </div>
  );
}
