import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Dashboard } from "@/components/Dashboard";
import { liveCharts } from "./chartMock";
import { fx, mockApi, renderApp } from "./helpers";

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
    expect(screen.getAllByText(/not NIFTY/).length).toBeGreaterThanOrEqual(1); // the index tile
    expect(screen.getByText(/No real money/)).toBeInTheDocument();

    // navigation
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Market Overview" })).toHaveAttribute("aria-current", "page");
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
    expect(within(menu).getByRole("link", { name: "Backtest" })).toHaveAttribute("href", "/backtest");
    expect(within(menu).getByRole("link", { name: "Reports" })).toHaveAttribute("href", "/reports");
    expect(within(menu).getByRole("link", { name: "Fundamentals" })).toHaveAttribute("href", "/fundamentals");
    for (const name of ["Trend Template", "Alerts"]) {
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
    expect(within(card).getAllByRole("img", { name: /^(green|amber|red|grey)$/ }).length).toBeGreaterThanOrEqual(1); // the index, leadership and breadth cards show figures, not dots
  });

  it("market health has no outer card; two columns: index and leadership, breadth and stage, setup flow and setups, feedback", async () => {
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
    expect(new Set(colours).size).toBe(7); // every card has its own border colour
    expect(names).toEqual([
      "Index price action", "Leadership", "Breadth", "Market stage", "Setups and breakouts",
      "Setups today", "Feedback loop",
    ]);
    expect(await within(overview).findByText(/Most setups are/)).toBeInTheDocument();
    expect(within(overview).queryByText("Details")).not.toBeInTheDocument(); // no Details link on any card
  });

  it("the index card has the chart on the left and three figures on the right, no score or details", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Index price action" });
    const figures = await within(card).findByLabelText("Index figures");
    expect(within(figures).getByText("VQI vs 200-day MA")).toBeInTheDocument();
    expect(within(figures).getByText("VQI vs 50-day MA")).toBeInTheDocument();
    expect(within(figures).getByText("Accumulation days")).toBeInTheDocument();
    expect(within(figures).getByText("Distribution days")).toBeInTheDocument();
    expect(within(figures).getAllByText("(last 25 sessions)")).toHaveLength(2);
    expect(within(card).queryByText("Details")).not.toBeInTheDocument();
    expect(within(card).queryByTitle("score out of 100")).not.toBeInTheDocument();
    expect(within(card).getByTestId("mini-index-price-chart")).toBeInTheDocument();
  });

  it("the market stage card charts the four stages and shows today's share of each", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Market stage" });
    expect(await within(card).findByTestId("mini-stage-chart")).toBeInTheDocument();
    const fig = within(await within(card).findByRole("complementary", { name: "Stage figures" }));
    for (const t of ["Stage 1: base", "Stage 2: uptrend", "Stage 3: top", "Stage 4: downtrend"])
      expect(fig.getByText(t)).toBeInTheDocument();
  });

  it("recent activity says which stocks joined or left the universe and why", async () => {
    mockApi();
    renderApp(<Dashboard />);
    expect(await screen.findByText(/Universe [\d,]+ to [\d,]+ stocks: \d+ joined, \d+ left/)).toBeInTheDocument();
    expect(await screen.findByText(/Left the universe: no longer in the NSE list/)).toBeInTheDocument();
    expect(await screen.findByText(/Joined the universe: was out before for price ₹19.8, below the ₹20 minimum/)).toBeInTheDocument();
  });

  it("paper trading and recent activity have their own colours and all eight cards are one size", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const paper = (await screen.findByText("Paper trading")).closest("section") as HTMLElement;
    const act = (await screen.findByText("Recent activity")).closest("section") as HTMLElement;
    expect(paper.className).toMatch(/border-\[#fb923c\]/);
    expect(act.className).toMatch(/border-\[#a3e635\]/);
    for (const card of [paper, act, await screen.findByRole("region", { name: "Setups today" })])
      expect(card.className).toMatch(/lg:h-\[23rem\]/);
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
    const fig = within(await screen.findByRole("complementary", { name: "Leadership figures" }));
    for (const t of ["New 52-week highs", "New 52-week lows", "Failed breakouts", "Leaders vs index"])
      expect(fig.getByText(t)).toBeInTheDocument();
    const bf = within(await screen.findByRole("complementary", { name: "Breadth figures" }));
    for (const t of ["Regime", "Above 50-day average", "Above 200-day average", "Net advancers"])
      expect(bf.getByText(t)).toBeInTheDocument();
    expect(screen.getByTestId("mini-trades-chart")).toBeInTheDocument();
    expect(await screen.findByTestId("mini-paper-progress")).toHaveTextContent("1 / 30");
    expect(await screen.findByTestId("mini-open-positions")).toHaveTextContent("BETA");
  });

  it("the setups and breakouts card counts today's breakouts and says since when it has data", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Setups and breakouts" });
    expect(await within(card).findByTestId("mini-setup-flow-chart")).toBeInTheDocument();
    const fig = within(within(card).getByRole("complementary", { name: "Setup flow figures" }));
    expect(fig.getByText("Breakouts").parentElement?.parentElement).toHaveTextContent("Breakouts1today");
    // the first stored scan has nothing to compare with: no count of new setups, a dash
    expect(fig.getByText("New setups").parentElement?.parentElement).toHaveTextContent("New setups—today");
    expect(card).toHaveTextContent("Failure rate: too few breakouts judged yet.");
    expect(card).toHaveTextContent("Since 5 Oct 2026, the first stored scan");
  });

  it("leadership charts net new highs with their 10-day average and lists today's new highs", async () => {
    const health = {
      ...fx.marketHealth,
      new_highs: [
        { symbol: "ALPHA", rs_rank: 91, sector: "Capital Goods" },
        { symbol: "BETA", rs_rank: null, sector: null },
      ],
    };
    mockApi({ "market/health": health });
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Leadership" });
    expect(await within(card).findByTestId("mini-highs-lows-chart")).toBeInTheDocument();
    expect(card).toHaveTextContent("Net new 52-week highs (highs − lows) a day");
    const list = within(card).getByTestId("new-highs-today");
    expect(list).toHaveTextContent("New highs today (2)");
    expect(within(list).getByRole("link", { name: /ALPHA/ })).toHaveAttribute("href", "/stocks/ALPHA");
    expect(within(list).getByRole("link", { name: /BETA/ })).toHaveTextContent("BETARS —");
    expect(screen.queryByRole("list", { name: "Divergence warnings" })).toBeNull();
  });

  it("shows divergence warnings above the cards only when there are some", async () => {
    const text = "VQI is 1.0% from its 52-week high, but the advance/decline line is 40% of its 52-week range below its own high: fewer stocks are taking part in the advance.";
    mockApi({ "market/health": { ...fx.marketHealth, warnings: [{ id: "ad_divergence", text }] } });
    renderApp(<Dashboard />);
    const box = await screen.findByRole("list", { name: "Divergence warnings" });
    expect(box).toHaveTextContent(`Divergence: ${text}`);
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
