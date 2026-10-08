import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Dashboard } from "@/components/Dashboard";
import { liveCharts } from "./chartMock";
import { mockApi, renderApp } from "./helpers";

describe("dashboard page (market view)", () => {
  it("shows the market panels and no watch list, stock chart or setup details", async () => {
    const calls = mockApi();
    renderApp(<Dashboard />);

    expect(await screen.findByRole("region", { name: "Market health overview" })).toBeInTheDocument();
    expect(await screen.findByText("Market stage")).toBeInTheDocument();
    expect(screen.queryByText("Top VCP setups")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("symbols").previousElementSibling).toHaveTextContent("63"));
    expect(await screen.findByText("Recent activity")).toBeInTheDocument();
    const breadthCard = await screen.findByRole("region", { name: "Breadth" });
    expect(await within(breadthCard).findByText("OFF")).toBeInTheDocument(); // the regime
    expect(within(breadthCard).getByText("31.7%")).toBeInTheDocument();
    expect(screen.queryByText("Market overview")).not.toBeInTheDocument();
    expect(await screen.findByText("Paper trading")).toBeInTheDocument();
    await waitFor(() => expect(liveCharts().length).toBeGreaterThanOrEqual(4)); // the four market health charts

    // moved to other pages
    expect(screen.queryByText("Watch list")).not.toBeInTheDocument();
    expect(screen.queryByText("VCP pattern")).not.toBeInTheDocument();
    expect(calls.some((c) => c.startsWith("stocks/"))).toBe(false);

    // honest labels
    expect(screen.getByText("Research tool, not financial advice")).toBeInTheDocument();
    expect(screen.getAllByText(/not NIFTY/).length).toBeGreaterThanOrEqual(2); // the index tile and the market health note
    expect(screen.getByText(/No real money/)).toBeInTheDocument();

    // navigation
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    for (const [name, href] of [["Screener", "/screener"], ["Stock Analysis", "/stocks"], ["Watchlist", "/watchlist"]] as const) {
      expect(within(nav).getByRole("link", { name })).toHaveAttribute("href", href);
    }
    expect(screen.queryByRole("complementary", { name: "Menu" })).not.toBeInTheDocument();
  });

  it("the logo opens a side bar with every page, the coming ones disabled, and it closes again", async () => {
    mockApi();
    renderApp(<Dashboard />);
    await userEvent.click(screen.getByRole("button", { name: "Open the menu" }));
    const menu = screen.getByRole("complementary", { name: "Menu" });
    expect(within(menu).getByRole("link", { name: "Watchlist" })).toHaveAttribute("href", "/watchlist");
    for (const name of ["Backtest", "Reports", "Fundamentals"]) {
      expect(within(menu).getByText(name).closest("[aria-disabled]")).toHaveAttribute("aria-disabled", "true");
    }
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("complementary", { name: "Menu" })).not.toBeInTheDocument();
  });

  it("the bottom bar carries the data status; today's date is at the far right of the top bar", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const status = await screen.findByLabelText("Data status");
    expect(within(status).getByText("Prices to")).toBeInTheDocument();
    expect(within(status).getByText("Last scan")).toBeInTheDocument();
    expect(within(status).getByText("Data copy")).toBeInTheDocument();
    const banner = screen.getAllByRole("banner")[0]!; // the top bar comes first
    expect(await within(banner).findByLabelText("Today")).toBeInTheDocument();
    expect(within(status.closest("footer") as HTMLElement).queryByLabelText("Today")).not.toBeInTheDocument();
    expect(screen.queryByText("Data of")).not.toBeInTheDocument();
  });

  it("the market health card shows four groups, each reading with a colour and a plain sentence", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Market health overview" });
    for (const title of ["Index price action", "Leadership", "Breadth", "Feedback loop"]) {
      expect(await within(card).findByRole("region", { name: title })).toBeInTheDocument();
    }
    expect(within(card).getAllByRole("img", { name: /^(green|amber|red|grey)$/ }).length).toBeGreaterThanOrEqual(7); // the index card shows figures, not dots
    expect(within(card).getByText(/changes no rule, scan or score/)).toBeInTheDocument();
  });

  it("market health has no outer card; two columns: index and leadership, breadth and feedback, stage and setups", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const overview = await screen.findByRole("region", { name: "Market health overview" });
    expect(overview.className).not.toMatch(/border|bg-panel/);
    expect(within(overview).queryByRole("heading", { name: "Market health" })).not.toBeInTheDocument();
    await screen.findByRole("region", { name: "Setups today" });
    const names = within(overview)
      .getAllByRole("region")
      .map((r) => r.getAttribute("aria-label") ?? within(r).queryByRole("heading")?.textContent);
    const colours = within(overview).getAllByRole("region").map((r) => /border-\[(#\w+)\]/.exec(r.className)?.[1]);
    expect(new Set(colours).size).toBe(6); // every card has its own border colour
    expect(names).toEqual([
      "Index price action", "Leadership", "Breadth", "Feedback loop", "Market stage", "Setups today",
    ]);
    expect(await within(overview).findByText(/Most setups are/)).toBeInTheDocument();
  });

  it("the index card has the chart on the left and three figures on the right, no score or details", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Index price action" });
    const figures = await within(card).findByLabelText("Index figures");
    expect(within(figures).getByText("Index vs 200-day average")).toBeInTheDocument();
    expect(within(figures).getByText("Accumulation days")).toBeInTheDocument();
    expect(within(figures).getByText("Distribution days")).toBeInTheDocument();
    expect(within(figures).getAllByText("(last 25 sessions)")).toHaveLength(2);
    expect(within(card).queryByText("Details")).not.toBeInTheDocument();
    expect(within(card).queryByTitle("score out of 100")).not.toBeInTheDocument();
    expect(within(card).getByTestId("mini-index-price-chart")).toBeInTheDocument();
  });

  it("the market stage card counts the scanned stocks by weekly stage", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = (await screen.findByText("Market stage")).closest("section") as HTMLElement;
    expect(await within(card).findByText(/Stage 2: uptrend/)).toBeInTheDocument();
    expect(within(card).getByText(/Most stocks are in Stage 2/)).toBeInTheDocument();
  });

  it("market health states a verdict with a 0 to 100 score", async () => {
    mockApi();
    renderApp(<Dashboard />);
    await waitFor(() => expect(screen.getByTestId("verdict-score")).not.toHaveTextContent("—"));
    expect(screen.getByTestId("verdict-label")).toBeInTheDocument();
    const score = Number(screen.getByTestId("verdict-score").textContent);
    expect(score).toBeGreaterThanOrEqual(0);
    expect(score).toBeLessThanOrEqual(100);
    expect(screen.getByTitle(/Pulling it down/)).toBeInTheDocument();
  });

  it("market health has four inner cards with charts", async () => {
    mockApi();
    renderApp(<Dashboard />);
    for (const t of ["Index price action", "Leadership", "Breadth", "Feedback loop"])
      expect(await screen.findByRole("region", { name: t })).toBeInTheDocument();
    for (const c of ["index-price-chart", "highs-lows-chart", "breadth-chart", "ad-line-chart"])
      expect(await screen.findByTestId(`mini-${c}`)).toBeInTheDocument();
    expect(screen.getByTestId("mini-trades-chart")).toBeInTheDocument();
    expect(await screen.findByTestId("mini-paper-progress")).toHaveTextContent("1 / 30");
    expect(await screen.findByTestId("mini-open-positions")).toHaveTextContent("BETA");
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
