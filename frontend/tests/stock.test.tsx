import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { StockAnalysis } from "@/components/StockAnalysis";
import { mockApi, renderApp } from "./helpers";

describe("stock analysis page", () => {
  it("shows the chart, the setup cards, the strategies and the history of the stock", async () => {
    const calls = mockApi();
    renderApp(<StockAnalysis symbol="BETA" />);
    expect(await screen.findByText("Setups across strategies")).toBeInTheDocument();
    expect(await screen.findByText("History and paper trades")).toBeInTheDocument();
    expect(await screen.findByText("Trend Template", { selector: "h2" })).toBeInTheDocument();
    expect(calls.some((c) => c.startsWith("stocks/BETA/history"))).toBe(true);
    expect(calls.some((c) => c.startsWith("stocks/BETA/bars"))).toBe(true);
    expect(screen.queryByText("Full analysis →")).not.toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Stock Analysis" })).toHaveAttribute("aria-current", "page");
  });

  it("says so when a stock has no breakout or paper trade", async () => {
    mockApi();
    renderApp(<StockAnalysis symbol="ALPHA" />);
    expect(await screen.findByText(/No breakout or paper trade for ALPHA/)).toBeInTheDocument();
  });

  it("another strategy can be shown from the strategies card", async () => {
    mockApi();
    renderApp(<StockAnalysis symbol="ALPHA" />);
    const card = (await screen.findByText("Setups across strategies")).closest("section") as HTMLElement;
    await userEvent.click(await within(card).findByRole("button", { name: /Flat base/i }));
    expect(within(card).getByRole("button", { name: /Flat base/i })).toHaveAttribute("aria-pressed", "true");
  });

  it("without a symbol it asks for a search", () => {
    mockApi();
    renderApp(<StockAnalysis symbol={null} />);
    expect(screen.getByText(/Search a symbol or company above/)).toBeInTheDocument();
  });
});
