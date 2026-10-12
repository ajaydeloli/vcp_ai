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
    expect(await within(breadthCard).findByText("OFF")).toBeInTheDocument(); // the strategies' regime
    expect(within(breadthCard).getByTestId("reading-breadth-above_50")).toHaveTextContent("72.0"); // the score's reading
    expect(screen.queryByText("Market overview")).not.toBeInTheDocument();
    expect(screen.queryByText("Paper trading")).not.toBeInTheDocument(); // its own page, /paper
    await waitFor(() => expect(liveCharts().length).toBeGreaterThanOrEqual(4)); // the four market health charts

    // moved to other pages
    expect(screen.queryByText("Watch list")).not.toBeInTheDocument();
    expect(screen.queryByText("VCP pattern")).not.toBeInTheDocument();
    expect(calls.some((c) => c.startsWith("stocks/"))).toBe(false);

    // honest labels
    expect(screen.getByText("Research tool, not financial advice")).toBeInTheDocument();
    expect(screen.getAllByText(/not NIFTY/).length).toBeGreaterThanOrEqual(1); // the index tile
    // "No real money" is on the Paper Trading page with the paper panel (tests/components.test.tsx)

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

  it("the cards show the score's own readings, coloured by their points", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Market health overview" });
    for (const title of ["Index trend", "Leadership", "Breadth", "Feedback loop"]) {
      expect(await within(card).findByRole("region", { name: title })).toBeInTheDocument();
    }
    // the same value and colour as the score card's pillar: 72% above the 50-day scores 100
    const v = within(card).getByTestId("reading-breadth-above_50");
    expect(v).toHaveTextContent("72.0");
    expect(v.className).toMatch(/text-up/);
    expect(within(card).getByTestId("reading-index_trend-nifty50")).toHaveTextContent("Yes");
    expect(within(card).getByTestId("reading-index_trend-stacked")).toHaveTextContent("2 of 2");
    expect(within(card).getByTestId("reading-leadership-leader_change")).toHaveTextContent("+45.0");
    // the paper trades keep their own reading with a dot
    expect(within(card).getAllByRole("img", { name: /^(green|amber|red|grey)$/ }).length).toBeGreaterThanOrEqual(1);
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
    expect(new Set(colours).size).toBe(10); // every card has its own border colour
    expect(names).toEqual([
      "Market health score", "Index trend", "Leadership", "Breadth", "Market stage", "Setups and breakouts",
      "Sector strength", "Sector rotation", "Feedback loop", "Setups today",
    ]);
    expect(await within(overview).findByText(/Most setups are/)).toBeInTheDocument();
    expect(within(overview).queryByText("Details")).not.toBeInTheDocument(); // no Details link on any card
  });

  it("the index card is NIFTY 500 with its averages and the score's index and volume readings", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Index trend" });
    const figures = await within(card).findByLabelText("Index figures");
    for (const t of ["Index trend", "Accumulation / distribution"]) expect(within(figures).getByRole("group", { name: `${t} readings` })).toBeInTheDocument();
    expect(within(figures).getByText("NIFTY 500 vs its 200-day average, %")).toBeInTheDocument();
    expect(within(figures).getByText("NIFTY 500 distribution days in 10")).toBeInTheDocument();
    expect(card).not.toHaveTextContent("VQI vs"); // VQI is only in the blended distribution count
    expect(within(card).getByTestId("mini-index-price-chart")).toBeInTheDocument();
    for (const t of ["NIFTY 500", "its 150-day", "NIFTY 50", "Smallcap 250"]) expect(card).toHaveTextContent(t);
    expect(card).toHaveTextContent("All lines start at 100.");
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

  it("recent activity has its own colour, the full width and the cards' height; no paper trading panel", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const act = (await screen.findByText("Recent activity")).closest("section") as HTMLElement;
    expect(act.className).toMatch(/border-\[#a3e635\]/);
    expect(act.parentElement?.className ?? "").not.toMatch(/grid-cols-2/); // alone in its row
    for (const card of [act, await screen.findByRole("region", { name: "Setups today" })])
      expect(card.className).toMatch(/lg:h-\[23rem\]/);
    expect(screen.queryByText("Paper trading")).not.toBeInTheDocument();
  });

  it("the score is shown once: the first row has NIFTY 500, not a score tile", async () => {
    mockApi();
    renderApp(<Dashboard />);
    expect(await screen.findByTestId("index-NIFTY 500")).toBeInTheDocument();
    expect(screen.queryByTestId("verdict-score")).not.toBeInTheDocument();
    expect(await screen.findByTestId("mhs-raw")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "Market health score" })).toHaveLength(1);
  });

  it("the Market health score card: label, caps, shadow light, pillars and history", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = within(await screen.findByRole("region", { name: "Market health score" }));
    expect(card.getByTestId("mhs-label")).toHaveTextContent("Mixed");
    expect(card.getByText(/, Strong; the label is capped/)).toBeInTheDocument();
    // the gauge: five label bands, the needle at the raw score, the number under it
    const gauge = card.getByTestId("mhs-gauge");
    expect(gauge.querySelectorAll("path")).toHaveLength(5);
    expect(card.getByTestId("mhs-needle")).toBeInTheDocument();
    expect(gauge.getAttribute("aria-label")).toMatch(/Market health score \d+ of 100, Mixed/);
    expect(Number(card.getByTestId("mhs-raw").textContent)).toBeGreaterThanOrEqual(80);
    expect(card.getByText(/Shadow light: Half size/)).toBeInTheDocument();
    expect(card.getByText(/Breadth thrust on/)).toBeInTheDocument();
    expect(card.getAllByText(/At most/)).toHaveLength(2);
    // the caps sit under the pillar bars, side by side
    const caps = card.getByRole("list", { name: "Caps" });
    expect(caps.closest('[aria-label="Pillars"]')).not.toBeNull();
    expect(caps.className).toMatch(/grid-cols-2/);
    for (const id of ["index_trend", "breadth", "leadership", "accumulation", "breakouts"])
      expect(card.getByTestId(`pillar-${id}`)).toBeInTheDocument();
    expect(card.getByTestId("pillar-breakouts")).toHaveTextContent("missing");
    expect(card.getByText("Stocks above their 50-day average, %")).toBeInTheDocument();
    expect(await screen.findByTestId("mini-market-health-score-history")).toBeInTheDocument();
    expect(card.getByRole("button", { name: "6M" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(card.getByRole("button", { name: "12M" }));
    expect(card.getByRole("button", { name: "12M" })).toHaveAttribute("aria-pressed", "true");
  });

  it("sector rotation: sectors on the four quadrants, tails, hover and the list", async () => {
    const pt = (w: number, ratio: number, momentum: number) => ({ day: `2026-0${7 + Math.floor(w / 4)}-${String(1 + (w % 4) * 7).padStart(2, "0")}`, ratio, momentum });
    const rotation = [
      { sector: "Information Technology", index_name: "Nifty IT", quadrant: "leading", points: [pt(0, 98, 99), pt(1, 99, 101), pt(2, 101, 102)] },
      { sector: "Metals & Mining", index_name: "Nifty Metal", quadrant: "lagging", points: [pt(0, 101, 99), pt(1, 99, 98), pt(2, 97, 96)] },
      { sector: "Power", index_name: "Nifty Power", quadrant: "improving", points: [pt(2, 99, 100.5)] },
    ];
    mockApi({ "market/health": { ...fx.marketHealth, rotation } });
    renderApp(<Dashboard />);
    const card = within(await screen.findByRole("region", { name: "Sector rotation" }));
    expect(card.getByTestId("rotation-chart")).toBeInTheDocument();
    for (const q of ["Leading", "Improving", "Weakening", "Lagging"]) expect(card.getByRole("region", { name: q })).toBeInTheDocument();
    expect(within(card.getByRole("region", { name: "Leading" })).getByText("Information Technology")).toBeInTheDocument();
    expect(within(card.getByRole("region", { name: "Weakening" })).getByText("None")).toBeInTheDocument();
    expect(card.getByText("IT")).toBeInTheDocument(); // the short label on the chart
    expect(card.getByText(/Shorter tails: Power/)).toBeInTheDocument();
    await userEvent.hover(card.getByTestId("rotation-Metals & Mining"));
    const tip = card.getByRole("tooltip");
    expect(tip).toHaveTextContent("Nifty Metal");
    expect(tip).toHaveTextContent("RS-Ratio 97.00");
    expect(card.getByRole("button", { name: "6w" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(card.getByRole("button", { name: "4w" }));
    expect(card.getByRole("button", { name: "4w" })).toHaveAttribute("aria-pressed", "true");
  });

  it("sector rotation without history says so", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = within(await screen.findByRole("region", { name: "Sector rotation" }));
    expect(card.getByText(/Not enough weekly history/)).toBeInTheDocument();
  });

  it("no stored score: the card says so", async () => {
    mockApi({ "market/health": { ...fx.marketHealth, score: null } });
    renderApp(<Dashboard />);
    expect(await screen.findByText(/No score stored yet/)).toBeInTheDocument();
    expect(screen.queryByTestId("mhs-needle")).not.toBeInTheDocument();
  });

  it("market health has four inner cards with charts", async () => {
    mockApi();
    renderApp(<Dashboard />);
    for (const t of ["Index trend", "Leadership", "Breadth", "Feedback loop"])
      expect(await screen.findByRole("region", { name: t })).toBeInTheDocument();
    for (const c of ["index-price-chart", "highs-lows-chart", "trend-template-chart", "breadth-chart", "ad-line-chart", "vqi-gap-chart"])
      expect(await screen.findByTestId(`mini-${c}`)).toBeInTheDocument();
    const fig = within(await screen.findByRole("complementary", { name: "Leadership figures" }));
    for (const t of ["Stocks passing the Trend Template, %", "Leaders vs 20 sessions ago, %", "Leaders of 20 sessions ago vs VQI, pp"])
      expect(fig.getByText(t)).toBeInTheDocument();
    const bf = within(await screen.findByRole("complementary", { name: "Breadth figures" }));
    for (const t of ["Strategy regime (frozen rule)", "Stocks above their 150-day average, %", "VQI minus NIFTY 500 over a month, pp"])
      expect(bf.getByText(t)).toBeInTheDocument();
    expect(screen.getByTestId("mini-trades-chart")).toBeInTheDocument();
    expect(screen.queryByTestId("mini-paper-progress")).toBeNull();
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
    expect(card).toHaveTextContent("since 5 Oct 2026, the first stored scan");
    // the score's breakout readings sit above our strategies' own figures
    expect(within(card).getByRole("group", { name: /Breakout environment \(score, 3 judged\) readings/ })).toBeInTheDocument();
    expect(fig.getByText("Our strategies")).toBeInTheDocument();
  });

  it("leadership charts the score's highs share and Trend Template share and lists today's new highs", async () => {
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
    expect(card).toHaveTextContent("New 52-week highs / (highs + lows), 10-day averages, %");
    const list = within(card).getByTestId("new-highs-today");
    expect(list).toHaveTextContent("New highs today (2)");
    expect(within(list).getByRole("link", { name: /ALPHA/ })).toHaveAttribute("href", "/stocks/ALPHA");
    expect(within(list).getByRole("link", { name: /BETA/ })).toHaveTextContent("BETARS —");
    expect(screen.queryByRole("list", { name: "Divergence warnings" })).toBeNull();
  });

  it("shows divergence warnings above the cards only when there are some", async () => {
    const text = "NIFTY 500 is near its 52-week high but the advance/decline line is well below its own.";
    mockApi({ "market/health": { ...fx.marketHealth, warnings: [{ id: "divergence", text }] } });
    renderApp(<Dashboard />);
    const box = await screen.findByRole("list", { name: "Divergence warnings" });
    expect(box).toHaveTextContent(`Divergence: ${text}`);
  });

  it("shows NIFTY 500's accumulation and distribution days as small dots on one line above the dates", async () => {
    const n = fx.marketHealth.score_points.length;
    const points = fx.marketHealth.score_points.map((p, i) => ({
      ...p,
      kind: i === n - 3 ? "accumulation" : i >= n - 2 ? "distribution" : null,
    }));
    mockApi({ "market/health": { ...fx.marketHealth, score_points: points } });
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Index trend" });
    expect(card).toHaveTextContent("● accumulation day");
    expect(card).toHaveTextContent("● distribution day");
    const chart = await waitFor(() => {
      const c = liveCharts().find((x) => x.el.dataset.testid === "mini-index-price-chart");
      expect(c?.series.some((s) => s.options.priceScaleId === "band")).toBe(true);
      return c!;
    });
    const band = chart.series.filter((s) => s.options.priceScaleId === "band");
    const byColor = Object.fromEntries(band.map((s) => [s.options.color, s.data.map((d) => [d.time, d.value])]));
    expect(byColor).toEqual({
      "#26a69a": [[points[n - 3]!.day, 0]],
      "#ef5350": [[points[n - 2]!.day, 0], [points[n - 1]!.day, 0]],
    });
    for (const s of band) {
      expect(s.options.lineVisible).toBe(false);
      expect(s.options.pointMarkersVisible).toBe(true);
      expect(s.options.pointMarkersRadius).toBeLessThanOrEqual(2);
    }
    expect(chart.scales["band"].scaleMargins.top).toBeGreaterThan(0.8);
    expect(chart.series[0]!.markers).toHaveLength(0); // nothing on the NIFTY 500 line
  });

  it("sector strength lists the sectors strongest first and the unclassified stocks last", async () => {
    mockApi();
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Sector strength" });
    const rows = within(card).getAllByRole("rowheader").map((r) => r.textContent);
    expect(rows.at(-1)).toBe("Not classified yet");
    expect(rows).toContain("Capital Goods");
    const cap = within(card).getByRole("rowheader", { name: "Capital Goods" }).closest("tr")!;
    expect(cap).toHaveTextContent("Capital Goods10%0 pp91"); // 1 stock, 0 %, no change, RS 91
    expect(cap).toHaveTextContent("+2.1%"); // Nifty Capital Goods over 21 sessions
    expect(card).toHaveTextContent("61 stocks are not classified yet");
    // the score's sector reading heads the table
    expect(within(card).getByTestId("sector-uptrend")).toHaveTextContent(/Sector indices in an uptrend \(score\): 85%/);
    // only the rows scroll: the title sits outside the scrolling box, the column headings stick
    const scroll = within(card).getByTestId("sector-scroll");
    expect(scroll.className).toMatch(/overflow-auto/);
    expect(scroll).not.toContainElement(within(card).getByRole("heading", { name: "Sector strength" }));
    expect(within(scroll).getAllByRole("rowgroup")[0]!.className).toMatch(/sticky top-0/);
  });

  it("breadth charts the thrust ratio with both lines and names the last thrust", async () => {
    const points = fx.marketHealth.score_points.map((p) => ({ ...p, thrust: false, thrust_ratio: 50 }));
    mockApi({ "market/health": { ...fx.marketHealth, score_points: points } });
    renderApp(<Dashboard />);
    const card = await screen.findByRole("region", { name: "Breadth" });
    expect(await within(card).findByTestId("mini-thrust-chart")).toBeInTheDocument();
    expect(within(card).getByTestId("thrust-note")).toHaveTextContent("No breadth thrust in this chart");
    const chart = await waitFor(() => {
      const c = liveCharts().find((x) => x.el.dataset.testid === "mini-thrust-chart");
      expect(c).toBeDefined();
      return c!;
    });
    await waitFor(() => expect(chart.series[0]?.priceLines.map((l) => l.price)).toEqual([40, 61.5]));
  });

  it("a thrust day is marked on the chart and named under it", async () => {
    const points = fx.marketHealth.score_points.map((p, i, all) => ({ ...p, thrust_ratio: 50 + i, thrust: i === all.length - 1 }));
    mockApi({ "market/health": { ...fx.marketHealth, score_points: points } });
    renderApp(<Dashboard />);
    const note = await screen.findByTestId("thrust-note");
    expect(note).toHaveTextContent(/^Last thrust 5 Oct 2026/);
    await waitFor(() => {
      const c = liveCharts().find((x) => x.el.dataset.testid === "mini-thrust-chart");
      expect(c?.series[0]?.markers.map((m) => m.time)).toEqual([points.at(-1)!.day]);
    });
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
