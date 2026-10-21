import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { TrendTemplatePage } from "@/components/TrendTemplate";
import { fx, mockApi, renderApp } from "./helpers";

const stock = (symbol: string, rs: number, failed: { name: string; measurement: number; threshold: number }[] = [], previous_stage: string | null = null) => ({
  symbol,
  company: null,
  sector: "Chemicals",
  stage: "STAGE_2",
  rs_rank: rs,
  close: 100,
  failed,
  previous_stage,
});

const DATA = {
  ...fx.trendTemplate,
  as_of: "2026-10-21",
  previous: "2026-10-19",
  week_ago: "2026-10-13",
  population: 1256,
  passing: 92,
  passing_previous: 137,
  conditions: [
    { condition_id: 7, name: "close_above_sma50", passed: 192, only_fail: 187 },
    { condition_id: 10, name: "rs_rank_min", passed: 376, only_fail: 1 },
  ],
  new_passers: [stock("DIVISLAB", 92)],
  dropped: [stock("OLDLEADER", 80, [{ name: "close_above_sma50", measurement: 95, threshold: 100 }])],
  nearly: [
    stock("AEROFLEX", 98, [{ name: "close_above_sma50", measurement: 488.95, threshold: 517.38 }]),
    stock("LAGGARD", 69, [{ name: "rs_rank_min", measurement: 69, threshold: 70 }]),
  ],
  nearly_total: 2,
  stages: [
    { sector: "All", counts: { STAGE_2: 373, STAGE_4: 349 }, total: 722 },
    { sector: "Chemicals", counts: { STAGE_2: 10, STAGE_1: 5 }, total: 15 },
  ],
  into_stage2: [stock("PNB", 75, [], "STAGE_1")],
};

describe("Trend Template page", () => {
  it("shows the headline, the funnel, new and dropped stocks, stage moves and sectors", async () => {
    mockApi({ "trend-template": DATA });
    renderApp(<TrendTemplatePage />);
    const head = await screen.findByRole("region", { name: "Trend Template today" });
    expect(head).toHaveTextContent("92");
    expect(head).toHaveTextContent("▼ 45 since 19 Oct 2026");
    const funnel = screen.getByRole("list", { name: "Stocks passing each condition" });
    expect(funnel).toHaveTextContent("Price above 50-day avg");
    expect(funnel).toHaveTextContent("187 only");
    expect(funnel).toHaveTextContent("All 10 conditions");
    expect(screen.getByRole("link", { name: "DIVISLAB" })).toHaveAttribute("href", "/stocks/DIVISLAB");
    expect(screen.getByText(/fails: Price above 50-day avg/)).toBeInTheDocument();
    expect(screen.getByText(/from Stage 1/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Weekly stage/ })).toHaveTextContent("Chemicals");
  });

  it("filters the one-short list by the failing condition and shows how far off each is", async () => {
    mockApi({ "trend-template": DATA });
    renderApp(<TrendTemplatePage />);
    expect(await screen.findByText("488.95 vs 517.38 needed (5.5% short)")).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Failing condition" });
    await userEvent.click(within(group).getByRole("button", { name: "RS rank 70 or more (1)" }));
    expect(screen.queryByRole("link", { name: "AEROFLEX" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "LAGGARD" })).toBeInTheDocument();
  });

  it("renders the API sample", async () => {
    mockApi();
    renderApp(<TrendTemplatePage />);
    expect(await screen.findByRole("region", { name: "Trend Template today" })).toBeInTheDocument();
  });
});
