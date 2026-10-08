// The API contract: every committed API sample (generated from the real API by
// tests/api/test_contract_samples.py) must satisfy its Zod schema, with no field the schema does
// not know, and broken responses must be rejected.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import * as s from "@/lib/schemas";

const dir = join(process.cwd(), "tests", "fixtures", "api"); // vitest runs from frontend/
const sample = (name: string): unknown => JSON.parse(readFileSync(join(dir, `${name}.json`), "utf8"));

const CASES: Record<string, z.ZodTypeAny> = {
  status: s.status,
  strategies: s.strategies,
  summary: s.summary,
  market: s.market,
  setups_vcp: s.setups,
  setups_flat_base: s.setups,
  overlap: s.overlap,
  bars: s.bars,
  stock_setups: s.stockSetups,
  activity: s.activity,
  paper: s.paper,
  search: s.search,
  screener: s.screener,
  stock_history: s.activity,
  market_health: s.marketHealth,
  live_quotes: s.liveQuotes,
  live_indices: s.liveIndices,
  live_status: s.liveStatus,
};

/** The same schema, but an unknown key anywhere is an error (a new API field must be noticed). */
function strictDeep(t: z.ZodTypeAny): z.ZodTypeAny {
  if (t instanceof z.ZodObject) {
    const shape = Object.fromEntries(
      Object.entries(t.shape as Record<string, z.ZodTypeAny>).map(([k, v]) => [k, strictDeep(v)]),
    );
    return z.object(shape).strict();
  }
  if (t instanceof z.ZodArray) return z.array(strictDeep(t.element));
  if (t instanceof z.ZodNullable) return strictDeep(t.unwrap()).nullable();
  if (t instanceof z.ZodOptional) return strictDeep(t.unwrap()).optional();
  return t;
}

describe("API samples", () => {
  for (const [name, schema] of Object.entries(CASES)) {
    it(`${name} matches its schema exactly`, () => {
      const r = strictDeep(schema).safeParse(sample(name));
      expect(r.success, r.success ? "" : JSON.stringify(r.error.issues[0])).toBe(true);
    });
  }

  it("every response carries as_of and data_time", () => {
    for (const name of Object.keys(CASES)) {
      const body = sample(name) as Record<string, unknown>;
      expect(body, name).toHaveProperty("as_of");
      expect(typeof body["data_time"], name).toBe("string");
    }
  });
});

describe("broken responses are rejected", () => {
  const mutate = (name: string, fn: (b: Record<string, any>) => void) => { // eslint-disable-line @typescript-eslint/no-explicit-any
    const b = structuredClone(sample(name)) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    fn(b);
    return b;
  };

  it("a number sent as a string", () => {
    const b = mutate("setups_vcp", (x) => (x.rows[0].score = "91"));
    expect(s.setups.safeParse(b).success).toBe(false);
  });

  it("a missing field", () => {
    const b = mutate("summary", (x) => delete x.universe_size);
    expect(s.summary.safeParse(b).success).toBe(false);
  });

  it("a missing value is null, and null is accepted where the API may send it", () => {
    const b = mutate("setups_vcp", (x) => {
      x.rows[0].pivot = null;
      x.rows[0].company = null;
    });
    expect(s.setups.safeParse(b).success).toBe(true);
  });

  it("null where the API never sends null (a required string)", () => {
    const b = mutate("setups_vcp", (x) => (x.rows[0].symbol = null));
    expect(s.setups.safeParse(b).success).toBe(false);
  });

  it("a date that is not YYYY-MM-DD", () => {
    const b = mutate("market", (x) => (x.days[0].day = "05/10/2026"));
    expect(s.market.safeParse(b).success).toBe(false);
  });
});
