import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { steps } from "@/components/AgentResearch";
import { AgentPage } from "@/components/Agent";
import type { AgentHypothesis } from "@/lib/schemas";
import { fx, mockApi, renderApp } from "./helpers";

const wi = (run_id: number, change: string, ic: number, top: number) => ({
  run_id,
  kind: "what_if",
  window: "development",
  start: "2022-02-01",
  end: "2024-06-30",
  ok: true,
  summary: `what-if ${change}`,
  error: null,
  change,
  what_if: {
    days: 125,
    setups: 6685,
    ic,
    ic_t: -1,
    top_per_day_pct: top,
    top_minus_bottom_fifth_pct: 0,
    all_mean_pct: 9.84,
    baseline_ic: -0.002,
    baseline_ic_t: -0.14,
    baseline_top_per_day_pct: 9.01,
    baseline_top_minus_bottom_fifth_pct: 0.28,
    top_kept_share: 0.8,
    horizon: 60,
    top_n: 5,
  },
});

const H1: AgentHypothesis = {
  hypothesis_id: 1,
  created_at: "2026-10-20T08:16:35Z",
  strategy: "vcp",
  title: "A pattern-heavier score ranks VCP setups better",
  metric: "IC",
  decision_rule: "IC >= baseline + 0.03",
  status: "CONCLUDED",
  outcome: "rejected",
  conclusion: "The VCP score is a display order, not an edge.",
  runs: [wi(1, "pattern 60", -0.003, 8.52), wi(2, "pattern 60, rs 0", -0.017, 7.66)],
};

const DATA = {
  ...fx.agent,
  available: true,
  hypotheses: [H1],
  budget_left: { vcp: 2, flat_base: 3 },
  review: { cycle: "2027-04", review_from: "2027-04-01", days_to_review: 161, max_per_strategy: 3, promotion_min_trades: 30, promotion_min_months: 3 },
};

describe("AI Agent page: research", () => {
  it("shows the review countdown, the budget and the promotion bar", async () => {
    mockApi({ agent: DATA });
    renderApp(<AgentPage />);
    const cycle = await screen.findByLabelText("Review cycle");
    expect(cycle).toHaveTextContent("161 days");
    expect(within(cycle).getByLabelText("vcp: 2 of 3 left")).toBeInTheDocument();
    expect(cycle).toHaveTextContent("at least 30 trades and 3 months");
    expect(cycle).toHaveTextContent("No hypothesis supported yet");
  });

  it("charts each variant against the frozen score and the average setup", async () => {
    mockApi({ agent: DATA });
    renderApp(<AgentPage />);
    const charts = await screen.findByLabelText("Variant results");
    expect(charts).toHaveTextContent("Frozen score");
    expect(charts).toHaveTextContent("V2: pattern 60, rs 0");
    expect(charts).toHaveTextContent("7.7%");
    expect(charts).toHaveTextContent("average of all setups: 9.8%");
    expect(charts).toHaveTextContent("-0.017");
    expect(screen.getByRole("list", { name: "Progress of H1" })).toHaveTextContent("Verdict: rejected");
  });

  it("marks the steps a rejected hypothesis never needed", () => {
    expect(steps(H1).map((s) => `${s.label}:${s.state}`)).toEqual([
      "Registered:done",
      "Development test:done",
      "Validation (once):skip",
      "Verdict: rejected:done",
      "Shadow trading:skip",
      "Review:skip",
    ]);
    const open = { ...H1, status: "OPEN", outcome: null, runs: [] };
    expect(steps(open).map((s) => s.state)).toEqual(["done", "now", "todo", "todo", "todo", "todo"]);
  });
});
