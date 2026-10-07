import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ChartView } from "@/components/ChartView";
import { KpiCards } from "@/components/KpiCards";
import { MarketOverview } from "@/components/MarketOverview";
import { PaperPanel } from "@/components/PaperPanel";
import { SetupOverview } from "@/components/SetupOverview";
import { SetupsTable } from "@/components/SetupsTable";
import { StatusBar } from "@/components/StatusBar";
import { StockPanel } from "@/components/StockPanel";
import { TopBar } from "@/components/TopBar";
import { liveCharts } from "./chartMock";
import { fx, mockApi, renderApp } from "./helpers";

const kpi = (title: string) => screen.getByTestId(`kpi-${title}`);

describe("KPI tiles", () => {
  it("count the lists of the VCP ranking and show the scan and the breadth", async () => {
    mockApi();
    renderApp(<KpiCards />);
    await waitFor(() => expect(kpi("A+ VCP setups")).toHaveTextContent("1"));
    expect(kpi("VCP setups")).toHaveTextContent("1");
    expect(kpi("Forming bases")).toHaveTextContent("0");
    expect(kpi("Breakout watch")).toHaveTextContent("2"); // ALPHA pivot ready, BETA broken out
    await waitFor(() => expect(screen.getByText("symbols").previousElementSibling).toHaveTextContent("63"));
    expect(screen.getByText("Trend Template").previousElementSibling).toHaveTextContent("3");
    await waitFor(() => expect(screen.getByTestId("kpi-breadth-above")).toHaveTextContent("31.7%"));
    expect(screen.getByTestId("kpi-regime")).toHaveTextContent("OFF");
    expect(screen.getByRole("img", { name: /above and below its 50-day average/ })).toBeInTheDocument();
  });

  it("a missing value is a dash, not 0", async () => {
    mockApi({
      summary: { ...fx.summary, universe_size: null, trend_template_pass: null, strategies: [] },
    });
    renderApp(<KpiCards />);
    await waitFor(() => expect(kpi("A+ VCP setups")).toHaveTextContent("1"));
    const symbols = screen.getByText("symbols").previousElementSibling!;
    expect(symbols).toHaveTextContent("—");
    expect(symbols).not.toHaveTextContent("0");
  });

  it("does not crash when the API is down", async () => {
    mockApi({ summary: new Error("down"), market: new Error("down"), "setups?strategy=vcp": new Error("down") });
    renderApp(<KpiCards />);
    await waitFor(() => expect(screen.getByTestId("kpi-regime")).toHaveTextContent("—"));
    expect(kpi("A+ VCP setups")).toHaveTextContent("—");
    expect(screen.getByTestId("kpi-breadth-above")).toHaveTextContent("—");
  });
});

describe("setups table", () => {
  const strategies = fx.strategies.strategies;
  const setup = (onSelect = vi.fn(), selected = null as { symbol: string; strategy: string } | null) =>
    renderApp(<SetupsTable strategies={strategies} selected={selected} onSelect={onSelect} />);
  const pick = (value: string) =>
    userEvent.selectOptions(screen.getByRole("combobox", { name: "Setup list" }), value);
  const symbols = () =>
    within(screen.getByRole("table"))
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.querySelector("td:nth-child(2) span")?.textContent ?? "");

  it("is a watch list, ranked best score first, with the VCP lists and an entry per other strategy, in a dropdown", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    expect(screen.getByText(/A watch list for research, not buy signals/)).toBeInTheDocument();
    expect(symbols()).toEqual(["ALPHA", "BETA"]);
    const list = screen.getByRole("combobox", { name: "Setup list" });
    const options = within(list).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual([
      "Top setups (2)", "A+ VCP (1)", "VCP (1)", "VCP like (0)", "Forming (0)", "Breakout watch (2)",
      "Flat base", "3 weeks tight", "Cup & handle", "Double bottom", "On several lists",
    ]); // fmt: skip
    expect(list).toHaveValue("list:top");
  });

  it("shows grade, status, pivot and the company name; a missing pivot is a dash", async () => {
    const rows = structuredClone(fx.setupsVcp.rows) as Record<string, unknown>[];
    rows[1] = { ...rows[1], pivot: null, pivot_distance_pct: null };
    mockApi({ "setups?strategy=vcp": { ...fx.setupsVcp, rows } });
    setup();
    await screen.findByText("ALPHA");
    const [first, second] = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(first).toHaveTextContent("Alpha Industries Ltd");
    expect(first).toHaveTextContent("A+ VCP");
    expect(first).toHaveTextContent("Pivot ready");
    expect(first).toHaveTextContent("120.00");
    expect(first).toHaveTextContent("171.90");
    expect(first).toHaveTextContent("+0.1%");
    expect(second).toHaveTextContent("—");
    expect(second).not.toHaveTextContent("0.00");
  });

  it("switches to another strategy's list", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    await pick("flat_base");
    await waitFor(() => expect(symbols()).toEqual(["GAMMA", "ALPHA"]));
  });

  it("sorts by a column and a missing value stays last in both directions", async () => {
    const rows = structuredClone(fx.setupsVcp.rows) as Record<string, unknown>[];
    rows[0] = { ...rows[0], rs_rank: null };
    mockApi({ "setups?strategy=vcp": { ...fx.setupsVcp, rows } });
    setup();
    await screen.findByText("ALPHA");
    await userEvent.click(screen.getByRole("button", { name: "Symbol" }));
    expect(symbols()).toEqual(["BETA", "ALPHA"]); // symbol, descending first
    await userEvent.click(screen.getByRole("button", { name: /Symbol/ }));
    expect(symbols()).toEqual(["ALPHA", "BETA"]);
    await userEvent.click(screen.getByRole("button", { name: "RS" }));
    expect(symbols()).toEqual(["BETA", "ALPHA"]); // ALPHA has no RS: last
    await userEvent.click(screen.getByRole("button", { name: /RS/ }));
    expect(symbols()).toEqual(["BETA", "ALPHA"]);
  });

  it("selecting a row reports the symbol and its strategy", async () => {
    const onSelect = vi.fn();
    mockApi();
    setup(onSelect);
    await userEvent.click((await screen.findByText("BETA")).closest("tr")!);
    expect(onSelect).toHaveBeenCalledWith({ symbol: "BETA", strategy: "vcp" });
  });

  it("marks the selected row", async () => {
    mockApi();
    setup(vi.fn(), { symbol: "BETA", strategy: "vcp" });
    const row = (await screen.findByText("BETA")).closest("tr")!;
    expect(row).toHaveAttribute("aria-selected", "true");
  });

  it("each VCP tab is a cut of the same ranking", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    await pick("list:aplus");
    await waitFor(() => expect(symbols()).toEqual(["ALPHA"]));
    await pick("list:vcp");
    await waitFor(() => expect(symbols()).toEqual(["BETA"]));
    await pick("list:watch");
    await waitFor(() => expect(symbols()).toEqual(["ALPHA", "BETA"]));
    await pick("list:forming");
    expect(await screen.findByText(/No setups in this list/)).toBeInTheDocument();
  });

  it("shows ten rows a page with page numbers, and a new list starts on page 1", async () => {
    const rows = Array.from({ length: 25 }, (_, i) => ({
      ...fx.setupsVcp.rows[0]!,
      symbol: `S${String(i + 1).padStart(2, "0")}`,
      instrument_id: `NSE_EQ|S${i + 1}`,
      score: 90 - i,
    }));
    mockApi({ "setups?strategy=vcp": { ...fx.setupsVcp, rows } });
    setup();
    await screen.findByText("S01");
    expect(symbols()).toHaveLength(10);
    expect(screen.getByText("Showing 1–10 of 25")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Page 2" }));
    expect(symbols()[0]).toBe("S11");
    expect(within(screen.getByRole("table")).getAllByRole("row")[1]).toHaveTextContent("11");
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(symbols()).toEqual(["S21", "S22", "S23", "S24", "S25"]);
    expect(screen.getByText("Showing 21–25 of 25")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(symbols()[0]).toBe("S11");
    await pick("flat_base");
    await waitFor(() => expect(screen.getByText(/Showing 1–2 of 2/)).toBeInTheDocument());
    await pick("list:top");
    await waitFor(() => expect(symbols()[0]).toBe("S01")); // back on page 1
  });

  it("On several lists shows every list a stock is on", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    await pick("overlap");
    const item = (await screen.findByText("ALPHA")).closest("li")!;
    expect(item).toHaveTextContent("VCP · 91.0");
    expect(item).toHaveTextContent("Flat base");
    expect(item).toHaveTextContent("Cup & handle");
  });

  it("an empty list says so", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    await pick("three_weeks_tight");
    expect(await screen.findByText(/No setups in this list/)).toBeInTheDocument();
  });

  it("an unreachable API is reported, not shown as an empty list", async () => {
    mockApi({ "setups?strategy=vcp": new TypeError("fetch failed") });
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("vcp api serve");
  });
});

function Harness({ first = "vcp" }: { first?: string }) {
  const [sel, setSel] = useState({ symbol: "ALPHA", strategy: first });
  return <StockPanel selection={sel} onSelect={setSel} />;
}

describe("stock panel", () => {
  it("is its own card: name, price, setup figures, and the chart with marks, pivot and stop", async () => {
    mockApi();
    renderApp(<Harness />);
    await screen.findByText("Alpha Industries Ltd");
    await waitFor(() => expect(liveCharts()).toHaveLength(1));
    const chart = liveCharts()[0]!;
    const candles = chart.of("candles")[0]!;
    await waitFor(() => expect(candles.data).toHaveLength(fx.bars.bars.length));
    expect(chart.of("line").map((l) => l.options.color)).toHaveLength(3);
    await waitFor(() => expect(candles.markers.length).toBeGreaterThan(0));
    // marks on days without a bar are dropped: the sample's base start (a Saturday) and T2
    expect(candles.markers.map((m) => m.text)).toEqual(["P1", "P2", "T1 9.2%"]);
    expect(candles.priceLines.map((l) => l.title).sort()).toEqual(["Pivot", "Stop"]);
    const vcp = fx.stockSetups.setups[0]!;
    expect(candles.priceLines.find((l) => l.title === "Pivot")?.price).toBe(vcp.pivot);

    expect(screen.getByText("171.90", { selector: "span" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("RS rank").nextElementSibling).toHaveTextContent("91"));
    expect(screen.getByText("Score").nextElementSibling).toHaveTextContent("91");
    expect(screen.getByText("Pivot").nextElementSibling).toHaveTextContent("120.00");
    expect(screen.getByText("A+ VCP")).toBeInTheDocument();
    for (const name of ["VCP", "Flat base", "Cup & handle"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("another strategy's chip changes the marks and the figures", async () => {
    mockApi();
    renderApp(<Harness />);
    await screen.findByText("Alpha Industries Ltd");
    await userEvent.click(screen.getByRole("button", { name: "Cup & handle" }));
    const candles = liveCharts()[0]!.of("candles")[0]!;
    await waitFor(() => expect(candles.markers.map((m) => m.text)).toEqual(expect.arrayContaining(["L", "B"])));
    expect(candles.markers.map((m) => m.text)).not.toContain("P1");
  });

  it("reports the strategy picked on a chip", async () => {
    const onSelect = vi.fn();
    mockApi();
    renderApp(<StockPanel selection={{ symbol: "ALPHA", strategy: "vcp" }} onSelect={onSelect} />);
    await screen.findByText("Alpha Industries Ltd");
    await userEvent.click(screen.getByRole("button", { name: "Flat base" }));
    expect(onSelect).toHaveBeenCalledWith({ symbol: "ALPHA", strategy: "flat_base" });
  });

  it("says when there is nothing selected", () => {
    mockApi();
    renderApp(<StockPanel selection={null} onSelect={() => {}} />);
    expect(screen.getByText(/Select a stock from the list/)).toBeInTheDocument();
  });

  it("a stock without bars is said so, not drawn", async () => {
    mockApi({ "stocks/NEWCO/bars?days=400": { ...fx.bars, symbol: "NEWCO", bars: [] } });
    renderApp(<StockPanel selection={{ symbol: "NEWCO", strategy: "vcp" }} onSelect={() => {}} />);
    expect(await screen.findByText(/No price bars for NEWCO/)).toBeInTheDocument();
    expect(liveCharts()).toHaveLength(0);
  });
});

describe("setup overview", () => {
  const overview = (strategy = "vcp") => renderApp(<SetupOverview selection={{ symbol: "ALPHA", strategy }} />);

  it("shows the VCP pattern: contractions, base figures and distance to the pivot", async () => {
    mockApi();
    overview();
    const card = (await screen.findByText("VCP pattern")).closest("section")!;
    const [t1, t2] = within(card).getAllByRole("row").slice(1);
    expect(t1).toHaveTextContent("T19.2%10First");
    expect(t2).toHaveTextContent("T24.7%10✓ Tighter");
    expect(card).toHaveTextContent("Base depth18.8%");
    await waitFor(() => expect(card).toHaveTextContent("Base duration50 days"));
    expect(card).toHaveTextContent("Pivot price120.00");
    expect(card).toHaveTextContent("Current price171.90");
    expect(card).toHaveTextContent("Distance to pivot1.2%");
  });

  it("a stock outside the ranking has dashes for base duration and distance, not 0", async () => {
    mockApi({ "setups?strategy=vcp": { ...fx.setupsVcp, rows: [] } });
    overview();
    const card = (await screen.findByText("VCP pattern")).closest("section")!;
    await waitFor(() => expect(card).toHaveTextContent("Pivot price120.00"));
    expect(card).toHaveTextContent("Base duration—");
    expect(card).toHaveTextContent("Distance to pivot—");
  });

  it("lists the Trend Template conditions with the weekly stage", async () => {
    mockApi();
    overview();
    const card = (await screen.findByText("Trend Template")).closest("section")!;
    expect(card).toHaveTextContent("3 / 3 ✓");
    expect(within(card).getAllByLabelText("passed")).toHaveLength(3);
    expect(card).toHaveTextContent("Stage 2");
  });

  it("says fundamentals are not available instead of showing numbers", async () => {
    mockApi();
    overview();
    const card = (await screen.findByText("Fundamentals")).closest("section")!;
    expect(card).toHaveTextContent("Not available");
    expect(card).toHaveTextContent("ROE—");
    expect(card).not.toHaveTextContent("0.0");
  });

  it("draws the score as a ring with one arc per component and the final score in the middle", async () => {
    mockApi();
    overview();
    const card = (await screen.findByText("Score breakdown")).closest("section")!;
    expect(within(card).getByRole("img", { name: /Final score 91/ })).toBeInTheDocument();
    expect(card).toHaveTextContent("Trend33.0 / 40");
    expect(card).toHaveTextContent("VCP17.0 / 40");
  });

  it("a setup with no score parts says so", async () => {
    mockApi();
    overview("flat_base");
    const card = (await screen.findByText("Score breakdown")).closest("section")!;
    expect(card).toHaveTextContent("No score parts stored");
  });
});

describe("chart range buttons", () => {
  it("mark the chosen range and refit short histories", async () => {
    const onRange = vi.fn();
    renderApp(<ChartView bars={fx.bars.bars} setup={null} range="1Y" onRange={onRange} />);
    expect(screen.getByRole("button", { name: "1Y" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "3M" }));
    expect(onRange).toHaveBeenCalledWith("3M");
    expect(liveCharts()[0]!.fitted).toBeGreaterThan(0); // 60 bars < 3 months: everything shown
  });

  it("show the last N bars as the visible range", () => {
    const many = Array.from({ length: 300 }, (_, i) => ({ ...fx.bars.bars[0]!, day: `2025-01-${String((i % 28) + 1).padStart(2, "0")}-${i}` }));
    const bars = many.map((b, i) => ({ ...b, day: `2025-${String(1 + Math.floor(i / 28)).padStart(2, "0")}-${String((i % 28) + 1).padStart(2, "0")}` }));
    renderApp(<ChartView bars={bars} setup={null} range="3M" onRange={() => {}} />);
    expect(liveCharts()[0]!.visibleRange).toEqual({ from: bars[300 - 63]!.day, to: bars[299]!.day });
  });
});

describe("market overview", () => {
  it("states the regime and labels the index as our universe, not NIFTY", async () => {
    mockApi();
    renderApp(<MarketOverview />);
    expect(await screen.findByText(/not NIFTY/)).toBeInTheDocument();
    expect(screen.getByText("OFF")).toBeInTheDocument();
    expect(screen.getByText("31.7%")).toBeInTheDocument();
    await waitFor(() => expect(liveCharts()).toHaveLength(2));
    const [index, breadth] = liveCharts();
    expect(index!.of("line").map((s) => s.options.title)).toEqual(["Index", "50-day average"]);
    const line = breadth!.of("line")[0]!;
    expect(line.priceLines[0]?.price).toBe(fx.market.breadth_threshold_pct);
  });
});

describe("paper panel", () => {
  it("shows results against the review, and says what is not computed yet", async () => {
    mockApi();
    renderApp(<PaperPanel />);
    expect(await screen.findByText(/Rule set paper-v1/)).toBeInTheDocument();
    expect(screen.getByText(/Review from 1 Apr 2027/)).toBeInTheDocument();
    const results = screen.getAllByRole("table")[0]!;
    const vcp = within(results).getByRole("cell", { name: "VCP" }).closest("tr")!;
    expect(vcp).toHaveTextContent("1 / 30");
    expect(vcp).toHaveTextContent("-5.15%");
    const idle = within(results).getByRole("cell", { name: "Flat base" }).closest("tr")!;
    expect(idle).toHaveTextContent("0 / 30");
    expect(within(idle).getAllByText("—").length).toBeGreaterThanOrEqual(3); // win %, avg, PF
    expect(screen.getByText(/Not computed yet: .*drawdown/)).toBeInTheDocument();
    const open = screen.getByText("Open positions").closest("div")!;
    expect(open).toHaveTextContent("BETA");
    expect(open).toHaveTextContent("92.00");
  });

  it("with no trades at all, says so", async () => {
    const strategies = fx.paper.strategies.map((s) => ({ ...s, closed: 0, open: [], win_rate_pct: null, avg_return_pct: null }));
    mockApi({ paper: { ...fx.paper, open_positions: 0, strategies } });
    renderApp(<PaperPanel />);
    expect(await screen.findByText(/No paper trades yet/)).toBeInTheDocument();
    expect(screen.queryByText("Open positions")).not.toBeInTheDocument();
  });
});

describe("status bar", () => {
  it("shows the warnings state and the disclaimer (data dates are in the navigation box)", async () => {
    mockApi();
    renderApp(<StatusBar />);
    expect(await screen.findByText("No warnings")).toBeInTheDocument();
    expect(screen.getByText("Research tool, not financial advice")).toBeInTheDocument();
  });

  it("lists the warnings", async () => {
    const warnings = ["No new prices for 2 sessions (latest 2026-10-05).", "cup_handle: no scan for 2026-10-05."];
    mockApi({ status: { ...fx.status, warnings } });
    renderApp(<StatusBar />);
    expect(await screen.findByText("2 warnings")).toBeInTheDocument();
    for (const w of warnings) expect(screen.getByText(w)).toBeInTheDocument();
  });

  it("tells how to start the API when it is not running", async () => {
    mockApi({ status: new TypeError("fetch failed") });
    renderApp(<StatusBar />);
    expect(await screen.findByRole("alert")).toHaveTextContent("vcp api serve");
    expect(screen.getByText("Research tool, not financial advice")).toBeInTheDocument();
  });
});

describe("top bar", () => {
  it("shows our own universe index, not NIFTY, with its day change", async () => {
    mockApi();
    renderApp(<TopBar onPick={() => {}} />);
    expect(await screen.findByText("106.07")).toBeInTheDocument();
    expect(screen.getByText("Our NSE universe index")).toBeInTheDocument();
    expect(screen.getByText("+0.10%")).toBeInTheDocument();
    expect(screen.queryByText(/NIFTY/i)).not.toBeInTheDocument();
  });
});

describe("top bar search", () => {
  it("finds a stock by name and reports it", async () => {
    const onPick = vi.fn();
    const calls = mockApi();
    renderApp(<TopBar onPick={onPick} />);
    await userEvent.type(screen.getByRole("searchbox"), "alp");
    const option = await screen.findByRole("option", { name: /ALPHA/ });
    await userEvent.click(within(option).getByRole("button"));
    expect(onPick).toHaveBeenCalledWith("ALPHA");
    expect(calls.some((c) => c.startsWith("search?q=alp"))).toBe(true);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("says when nothing matches", async () => {
    mockApi({ "search?q=zzz&limit=8": { ...fx.search, query: "zzz", results: [] } });
    renderApp(<TopBar onPick={() => {}} />);
    await userEvent.type(screen.getByRole("searchbox"), "zzz");
    expect(await screen.findByText("No match")).toBeInTheDocument();
  });
});
