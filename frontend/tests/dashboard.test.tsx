import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Dashboard } from "@/components/Dashboard";
import { liveCharts } from "./chartMock";
import { mockApi, renderApp } from "./helpers";

describe("dashboard page", () => {
  it("shows every panel, opens on the best VCP setup, and states what it is", async () => {
    mockApi();
    renderApp(<Dashboard />);

    // KPI cards, list, chart, details, activity, market, paper, status bar
    expect(await screen.findByText("Watch list")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("symbols").previousElementSibling).toHaveTextContent("63"));
    expect(await screen.findByText("Alpha Industries Ltd", { selector: "span.text-sm" })).toBeInTheDocument();
    expect(await screen.findByText("Recent activity")).toBeInTheDocument();
    expect(await screen.findByText("Market overview")).toBeInTheDocument();
    expect(await screen.findByText("Paper trading")).toBeInTheDocument();
    expect((await screen.findAllByText("Trend Template")).length).toBeGreaterThan(0);
    await waitFor(() => expect(liveCharts().length).toBeGreaterThanOrEqual(3)); // price + 2 market

    // honest labels
    expect(screen.getByText("Research tool, not financial advice")).toBeInTheDocument();
    expect(screen.getByText(/not NIFTY/)).toBeInTheDocument();
    expect(screen.getByText(/A watch list for research, not buy signals/)).toBeInTheDocument();
    expect(screen.getByText(/No real money/)).toBeInTheDocument();

    // navigation: only the dashboard exists yet
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    for (const name of ["Screener", "Watchlist", "Backtest", "Reports"]) {
      expect(within(nav).getByText(name).closest("[aria-disabled]")).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("clicking another row opens that stock in the chart panel", async () => {
    mockApi();
    renderApp(<Dashboard />);
    // BETA also appears in the activity and paper panels; the list's rows carry aria-selected
    await waitFor(() => expect(document.querySelector("tr[aria-selected]")).not.toBeNull());
    const row = [...document.querySelectorAll("tr[aria-selected]")].find((r) => r.textContent?.includes("BETA"));
    await userEvent.click(row!);
    await waitFor(() => {
      const heading = screen.getAllByText("BETA", { selector: "span.text-2xl" });
      expect(heading).toHaveLength(1);
    });
  });

  it("searching a symbol opens it in the chart panel", async () => {
    mockApi();
    renderApp(<Dashboard />);
    await screen.findByText("Watch list");
    await userEvent.type(screen.getByRole("searchbox"), "gamm");
    // the sample search answers ALPHA for any text; the panel opens whatever was picked
    await userEvent.click(within(await within(await screen.findByRole("listbox", { name: "Search results" })).findByRole("option")).getByRole("button"));
    await waitFor(() => expect(screen.getAllByText("ALPHA", { selector: "span.text-2xl" })).toHaveLength(1));
  });

  it("reports an unreachable API instead of an empty page", async () => {
    mockApi({
      strategies: new TypeError("fetch failed"),
      status: new TypeError("fetch failed"),
      summary: new TypeError("fetch failed"),
      market: new TypeError("fetch failed"),
      paper: new TypeError("fetch failed"),
      activity: new TypeError("fetch failed"),
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
