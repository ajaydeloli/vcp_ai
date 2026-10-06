import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChartView } from "@/components/ChartView";
import { KpiCards } from "@/components/KpiCards";
import { MarketOverview } from "@/components/MarketOverview";
import { PaperPanel } from "@/components/PaperPanel";
import { SetupsTable } from "@/components/SetupsTable";
import { StatusBar } from "@/components/StatusBar";
import { StockPanel } from "@/components/StockPanel";
import { TopBar } from "@/components/TopBar";
import { liveCharts } from "./chartMock";
import { fx, mockApi, renderApp } from "./helpers";

const kpi = (title: string) => screen.getByTestId(`kpi-${title}`);

describe("KPI cards", () => {
  it("show the summary, regime and paper numbers", async () => {
    mockApi();
    renderApp(<KpiCards />);
    await waitFor(() => expect(kpi("Universe scanned")).toHaveTextContent("63"));
    expect(screen.getByText("3 pass the Trend Template")).toBeInTheDocument();
    const grade2 = fx.summary.strategies.reduce((a, s) => a + s.grade2_plus, 0);
    await waitFor(() => expect(kpi("Grade 2+ setups")).toHaveTextContent(String(grade2)));
    expect(kpi("Breakouts today")).toHaveTextContent("1");
    await waitFor(() => expect(kpi("Market regime")).toHaveTextContent("OFF"));
    expect(screen.getByText(/Breadth 31\.7%/)).toBeInTheDocument();
    await waitFor(() => expect(kpi("Paper positions open")).toHaveTextContent("1"));
    expect(screen.getByText("1 closed so far")).toBeInTheDocument();
  });

  it("a missing value is a dash, not 0", async () => {
    mockApi({
      summary: { ...fx.summary, universe_size: null, trend_template_pass: null, strategies: [] },
    });
    renderApp(<KpiCards />);
    await waitFor(() => expect(kpi("Grade 2+ setups")).toHaveTextContent("0"));
    expect(kpi("Universe scanned")).toHaveTextContent("—");
    expect(kpi("Universe scanned")).not.toHaveTextContent("0");
  });

  it("does not crash when the API is down", async () => {
    mockApi({ summary: new Error("down"), market: new Error("down"), paper: new Error("down") });
    renderApp(<KpiCards />);
    await waitFor(() => expect(kpi("Market regime")).toHaveTextContent("—"));
    expect(kpi("Paper positions open")).toHaveTextContent("—");
  });
});

describe("setups table", () => {
  const strategies = fx.strategies.strategies;
  const setup = (onSelect = vi.fn(), selected = null as { symbol: string; strategy: string } | null) =>
    renderApp(<SetupsTable strategies={strategies} summary={fx.summary} selected={selected} onSelect={onSelect} />);
  const symbols = () =>
    within(screen.getByRole("table"))
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.querySelector("td:nth-child(2) span.font-medium")?.textContent ?? "");

  it("is a watch list, ranked best score first, with a tab per strategy", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    expect(screen.getByText(/A watch list for research, not buy signals/)).toBeInTheDocument();
    expect(symbols()).toEqual(["ALPHA", "BETA"]);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(
      expect.arrayContaining(["VCP2", "Flat base2", "3 weeks tight0", "Cup & handle1", "Double bottom0", "Breakouts"]),
    );
    expect(screen.getByRole("tab", { name: /VCP/ })).toHaveAttribute("aria-selected", "true");
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
    expect(second).toHaveTextContent("—");
    expect(second).not.toHaveTextContent("0.00");
  });

  it("switches to another strategy's list", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    await userEvent.click(screen.getByRole("tab", { name: /Flat base/ }));
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

  it("Breakouts collects the breakout status of every list, with a strategy column", async () => {
    const calls = mockApi();
    setup();
    await screen.findByText("ALPHA");
    await userEvent.click(screen.getByRole("tab", { name: "Breakouts" }));
    await waitFor(() => expect(symbols()).toEqual(["BETA"]));
    expect(screen.getByRole("button", { name: "Strategy" })).toBeInTheDocument();
    expect(calls.filter((c) => c.includes("status=BREAKOUT"))).toHaveLength(strategies.length);
  });

  it("On several lists shows every list a stock is on", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    await userEvent.click(screen.getByRole("tab", { name: /On several lists/ }));
    const item = (await screen.findByText("ALPHA")).closest("li")!;
    expect(item).toHaveTextContent("VCP · 91.0");
    expect(item).toHaveTextContent("Flat base");
    expect(item).toHaveTextContent("Cup & handle");
  });

  it("an empty list says so", async () => {
    mockApi();
    setup();
    await screen.findByText("ALPHA");
    await userEvent.click(screen.getByRole("tab", { name: /3 weeks tight/ }));
    expect(await screen.findByText(/No setups in this list/)).toBeInTheDocument();
  });

  it("an unreachable API is reported, not shown as an empty list", async () => {
    mockApi({ "setups?strategy=vcp": new TypeError("fetch failed") });
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("vcp api serve");
  });
});

describe("stock panel", () => {
  it("draws the chart with the strategy's marks, pivot and stop, and lists the setups", async () => {
    mockApi();
    renderApp(<StockPanel selection={{ symbol: "ALPHA", strategy: "vcp" }} />);
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

    for (const name of ["VCP", "Flat base", "Cup & handle"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("3 / 3")).toBeInTheDocument(); // Trend Template conditions passed
    expect(screen.getByText("Stage 2")).toBeInTheDocument();
  });

  it("another strategy's chip changes the marks", async () => {
    mockApi();
    renderApp(<StockPanel selection={{ symbol: "ALPHA", strategy: "vcp" }} />);
    await screen.findByText("Alpha Industries Ltd");
    await userEvent.click(screen.getByRole("button", { name: "Cup & handle" }));
    const candles = liveCharts()[0]!.of("candles")[0]!;
    await waitFor(() => expect(candles.markers.map((m) => m.text)).toEqual(expect.arrayContaining(["L", "B"])));
    expect(candles.markers.map((m) => m.text)).not.toContain("P1");
  });

  it("says when there is nothing selected", () => {
    mockApi();
    renderApp(<StockPanel selection={null} />);
    expect(screen.getByText(/Select a stock from the list/)).toBeInTheDocument();
  });

  it("a stock without bars is said so, not drawn", async () => {
    mockApi({ "stocks/NEWCO/bars?days=400": { ...fx.bars, symbol: "NEWCO", bars: [] } });
    renderApp(<StockPanel selection={{ symbol: "NEWCO", strategy: "vcp" }} />);
    expect(await screen.findByText(/No price bars for NEWCO/)).toBeInTheDocument();
    expect(liveCharts()).toHaveLength(0);
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
  it("shows the data dates, the copy time and the disclaimer", async () => {
    mockApi();
    renderApp(<StatusBar />);
    expect(await screen.findByText("No warnings")).toBeInTheDocument();
    expect(screen.getAllByText("5 Oct 2026", { selector: "span.text-ink" })).toHaveLength(2); // prices, scans
    expect(screen.getByText("6 Oct, 14:00 IST")).toBeInTheDocument();
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
