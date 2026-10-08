import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ChartView } from "@/components/ChartView";
import { SetupCounts } from "@/components/SetupCounts";
import { Tiles } from "@/components/Tiles";
import { PaperPanel } from "@/components/PaperPanel";
import { SetupOverview } from "@/components/SetupOverview";
import { StatusBar } from "@/components/StatusBar";
import { StockPanel } from "@/components/StockPanel";
import { TopBar } from "@/components/TopBar";
import { liveCharts } from "./chartMock";
import { fx, mockApi, renderApp } from "./helpers";

const kpi = (title: string) => screen.getByTestId(`kpi-${title}`);

describe("setup counts", () => {
  it("count the lists of the VCP ranking", async () => {
    mockApi();
    renderApp(<SetupCounts />);
    await waitFor(() => expect(kpi("A+ VCP setups")).toHaveTextContent("1"));
    expect(kpi("VCP setups")).toHaveTextContent("1");
    expect(kpi("Forming bases")).toHaveTextContent("0");
    expect(kpi("Breakout watch")).toHaveTextContent("2"); // ALPHA pivot ready, BETA broken out
  });

  it("an unreachable API is reported, not shown as 0", async () => {
    mockApi({ "setups?strategy=vcp": new Error("down") });
    renderApp(<SetupCounts />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("kpi-A+ VCP setups")).not.toBeInTheDocument();
  });
});

describe("tiles", () => {
  it("show the scan, our own index (not NIFTY) and the market health score", async () => {
    mockApi();
    renderApp(<Tiles />);
    await waitFor(() => expect(screen.getByText("symbols").previousElementSibling).toHaveTextContent("63"));
    expect(screen.getByText("Trend Template").previousElementSibling).toHaveTextContent("3");
    expect(await screen.findByText("106.07")).toBeInTheDocument();
    expect(screen.getByText("VCP Universe Index")).toBeInTheDocument();
    expect(screen.getByText("+0.10%")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("verdict-score")).not.toHaveTextContent("—"));
    expect(screen.getByTestId("verdict-label").textContent).toMatch(/\w/);
  });

  it("NIFTY 50 and SENSEX say they need an index feed instead of showing a number", async () => {
    mockApi();
    renderApp(<Tiles />);
    expect(screen.getByTestId("index-NIFTY 50")).toHaveTextContent("—");
    expect(screen.getByTestId("index-SENSEX")).toHaveTextContent("—");
    expect(screen.getAllByText("Needs an index feed")).toHaveLength(2);
  });

  it("a missing scan is a dash, not 0", async () => {
    mockApi({ summary: { ...fx.summary, universe_size: null, trend_template_pass: null, strategies: [] } });
    renderApp(<Tiles />);
    await waitFor(() => expect(screen.getByText("symbols").previousElementSibling).toHaveTextContent("—"));
    expect(screen.getByText("symbols").previousElementSibling).not.toHaveTextContent("0");
  });

  it("does not crash when the API is down", async () => {
    mockApi({ summary: new Error("down"), market: new Error("down"), "market/health": new Error("down") });
    renderApp(<Tiles />);
    await waitFor(() => expect(screen.getByTestId("verdict-score")).toHaveTextContent("—"));
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
