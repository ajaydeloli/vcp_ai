import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Dashboard } from "@/components/Dashboard";
import { liveCharts } from "./chartMock";
import { mockApi, renderApp } from "./helpers";

describe("dashboard page (market overview)", () => {
  it("shows the market panels and no watch list, stock chart or setup details", async () => {
    const calls = mockApi();
    renderApp(<Dashboard />);

    expect(await screen.findByText("Market health")).toBeInTheDocument();
    expect(await screen.findByText("Market stage")).toBeInTheDocument();
    expect(screen.queryByText("Top VCP setups")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("symbols").previousElementSibling).toHaveTextContent("63"));
    expect(await screen.findByText("Recent activity")).toBeInTheDocument();
    expect(await screen.findByText("Market overview")).toBeInTheDocument();
    expect(await screen.findByText("Paper trading")).toBeInTheDocument();
    await waitFor(() => expect(liveCharts().length).toBeGreaterThanOrEqual(4)); // the four market health charts

    // moved to other pages
    expect(screen.queryByText("Watch list")).not.toBeInTheDocument();
    expect(screen.queryByText("VCP pattern")).not.toBeInTheDocument();
    expect(calls.some((c) => c.startsWith("stocks/"))).toBe(false);

    // honest labels
    expect(screen.getByText("Research tool, not financial advice")).toBeInTheDocument();
    expect(screen.getAllByText(/not NIFTY/).length).toBeGreaterThanOrEqual(2); // market health and market overview
    expect(screen.getByText(/No real money/)).toBeInTheDocument();

    // navigation
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    for (const [name, href] of [["Screener", "/screener"], ["Stock Analysis", "/stocks"], ["Watchlist", "/watchlist"]] as const) {
      expect(within(nav).getByRole("link", { name })).toHaveAttribute("href", href);
    }
    for (const name of ["Backtest", "Reports"]) {
      expect(within(nav).getByText(name).closest("[aria-disabled]")).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("the market health card shows four groups, each reading with a colour and a plain sentence", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = (await screen.findByText("Market health")).closest("section") as HTMLElement;
    for (const title of ["Index price action", "Leadership", "Breadth", "Feedback loop"]) {
      expect(await within(card).findByText(title)).toBeInTheDocument();
    }
    expect(within(card).getAllByRole("img", { name: /^(green|amber|red|grey)$/ }).length).toBeGreaterThanOrEqual(8);
    expect(within(card).getByText(/changes no rule, scan or score/)).toBeInTheDocument();
  });

  it("the market stage card counts the scanned stocks by weekly stage", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = (await screen.findByText("Market stage")).closest("section") as HTMLElement;
    expect(await within(card).findByText(/Stage 2: uptrend/)).toBeInTheDocument();
    expect(within(card).getByText(/Most stocks are in Stage 2/)).toBeInTheDocument();
  });

  it("market health has four inner cards with charts", async () => {
    mockApi();
    renderApp(<Dashboard />);
    for (const t of ["Index price action", "Leadership", "Breadth", "Feedback loop"])
      expect(await screen.findByRole("region", { name: t })).toBeInTheDocument();
    for (const c of ["index-price-chart", "highs-lows-chart", "breadth-chart", "ad-line-chart"])
      expect(await screen.findByTestId(`mini-${c}`)).toBeInTheDocument();
    expect(screen.getByTestId("mini-trades-chart")).toBeInTheDocument();
  });

  it("reports an unreachable API instead of an empty page", async () => {
    mockApi({
      strategies: new TypeError("fetch failed"),
      status: new TypeError("fetch failed"),
      summary: new TypeError("fetch failed"),
      market: new TypeError("fetch failed"),
      paper: new TypeError("fetch failed"),
      activity: new TypeError("fetch failed"),
      screener: new TypeError("fetch failed"),
      "market/health": new TypeError("fetch failed"),
      "setups?strategy=vcp": new TypeError("fetch failed"),
    });
    renderApp(<Dashboard />);
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(3));
    for (const a of screen.getAllByRole("alert")) expect(a).toHaveTextContent(/vcp api serve/);
    expect(screen.getByText("Research tool, not financial advice")).toBeInTheDocument();
  });

  it("a response that breaks the contract is rejected, not displayed", async () => {
    mockApi({ summary: { as_of: "2026-10-05", data_time: "x", universe_size: "63" } });
    renderApp(<Dashboard />);
    await waitFor(() => expect(screen.getByText("symbols").previousElementSibling).toHaveTextContent("—"));
    expect(screen.getByText("symbols").previousElementSibling).not.toHaveTextContent("63");
  });
});
