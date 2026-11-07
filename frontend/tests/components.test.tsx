import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SetupCounts } from "@/components/SetupCounts";
import { Tiles } from "@/components/Tiles";
import { ScoreGauge } from "@/components/ScoreGauge";
import { PaperPanel } from "@/components/PaperPanel";
import { FundamentalsCard } from "@/components/Fundamentals";
import { ScoreCard, TrendCard } from "@/components/SetupOverview";
import { StatusBar } from "@/components/StatusBar";
import { TopBar } from "@/components/TopBar";
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
  it("show the scan, our own index (not NIFTY) and NIFTY 500", async () => {
    const live = structuredClone(fx.liveIndices);
    const n500 = live.indices.find((i) => i.id === "NIFTY500");
    Object.assign(n500 ?? {}, { ...live.indices[0], id: "NIFTY500", label: "NIFTY 500", last_price: 21909.7, change: 43.05, change_pct: 0.2 });
    mockApi({ "live/indices": live });
    renderApp(<Tiles />);
    await waitFor(() => expect(screen.getByText("symbols").previousElementSibling).toHaveTextContent("63"));
    expect(screen.getByText("Trend Template").previousElementSibling).toHaveTextContent("3");
    expect(await screen.findByText("106.07")).toBeInTheDocument();
    expect(screen.getByText("VCP Quality Index (VQI)")).toBeInTheDocument();
    expect(screen.getByText("+0.10%")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("index-NIFTY 500")).toHaveTextContent("21,909.70"));
    expect(screen.getByTestId("index-change-NIFTY 500")).toHaveTextContent("+0.20%");
    expect(screen.queryByTestId("verdict-score")).not.toBeInTheDocument(); // the score has its own card
  });

  it("NIFTY 50 and SENSEX show the live value, the change and how old it is", async () => {
    mockApi();
    renderApp(<Tiles />);
    await waitFor(() => expect(screen.getByTestId("index-NIFTY 50")).toHaveTextContent("25,500"));
    expect(screen.getByTestId("index-change-NIFTY 50")).toHaveTextContent("+2.00%");
    expect(screen.getByTestId("index-note-NIFTY 50")).toHaveTextContent("Live, delayed 0 s");
    expect(screen.getByTestId("index-SENSEX")).toHaveTextContent("83,000");
  });

  it("without a token the index tiles show a dash and why, never 0", async () => {
    const down = structuredClone(fx.liveIndices);
    down.mode = "unavailable";
    down.feed = { ...down.feed, state: "token_needed", message: "Live feed needs a fresh token" };
    for (const i of down.indices) Object.assign(i, { available: false, last_price: null, change: null, change_pct: null, mode: null, points: [] });
    mockApi({ "live/indices": down });
    renderApp(<Tiles />);
    await waitFor(() => expect(screen.getByTestId("index-note-NIFTY 50")).toHaveTextContent("fresh token"));
    expect(screen.getByTestId("index-NIFTY 50")).toHaveTextContent("—");
    expect(screen.getByTestId("index-SENSEX")).not.toHaveTextContent("0");
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
    await waitFor(() => expect(screen.getByTestId("index-NIFTY 500")).toHaveTextContent("—"));
  });
});

describe("the cards of a stock (Chart page tabs)", () => {
  const stock = { ...fx.stockSetups, symbol: "ALPHA" } as never;

  it("lists the Trend Template conditions with the weekly stage", () => {
    renderApp(<TrendCard stock={stock} />);
    const card = screen.getByText("Trend Template").closest("section")!;
    expect(card).toHaveTextContent("3 / 3 ✓");
    expect(within(card).getAllByLabelText("passed")).toHaveLength(3);
    expect(card).toHaveTextContent("Stage 2");
  });

  it("shows the latest quarter's fundamentals; a missing metric is a dash with its reason, never 0", async () => {
    mockApi();
    renderApp(<FundamentalsCard symbol="ALPHA" />);
    const card = (await screen.findByText("Fundamentals")).closest("section")!;
    await waitFor(() => expect(card).toHaveTextContent("Jun 26 quarter · consolidated · published 20 Jul 2026"));
    expect(card).toHaveTextContent("● Current");
    expect(card).toHaveTextContent("EPS (quarter / TTM)3.00 / 10.50");
    expect(card).toHaveTextContent("EPS growth YoY+50.0%");
    expect(card).toHaveTextContent("Sales growth YoY+25.0%");
    expect(card).toHaveTextContent("Debt / Equity0.25");
    expect(card).toHaveTextContent("ROE (TTM)2.6%"); // a level: no sign, no up/down colour
    // no Mar-25 quarter: EPS growth a quarter ago is unknown, so acceleration is missing
    expect(card).toHaveTextContent("EPS acceleration—");
    expect(within(card).getByLabelText("Not available: Not enough quarters filed yet")).toBeInTheDocument();
    expect(card).toHaveTextContent("Data: 6 of 7 metrics");
  });

  it("a stock without results says so instead of showing numbers", async () => {
    mockApi();
    renderApp(<FundamentalsCard symbol="GAMMA" />);
    const card = (await screen.findByText("Fundamentals")).closest("section")!;
    await waitFor(() => expect(card).toHaveTextContent("No results filed yet"));
    expect(card).not.toHaveTextContent("0.0");
  });

  it("draws the score as a ring with one arc per component and the final score in the middle", () => {
    renderApp(<ScoreCard setup={fx.stockSetups.setups[0] as never} />);
    const card = screen.getByText("Score breakdown").closest("section")!;
    expect(within(card).getByRole("img", { name: /Final score 91/ })).toBeInTheDocument();
    expect(card).toHaveTextContent("Trend33.0 / 40");
    expect(card).toHaveTextContent("VCP17.0 / 40");
  });
});

describe("paper panel", () => {
  it("shows results against the review, and says what is not computed yet", async () => {
    mockApi();
    renderApp(<PaperPanel />);
    expect(await screen.findByText(/Rule set paper-v1/)).toBeInTheDocument();
    expect(screen.getByText(/Review from 1 Apr 2027/)).toBeInTheDocument();
    expect(screen.getByText(/No real money/)).toBeInTheDocument(); // the honest label travels with the panel
    const results = screen.getAllByRole("table")[0]!;
    const vcp = within(results).getByRole("cell", { name: "VCP" }).closest("tr")!;
    expect(vcp).toHaveTextContent("1 / 30");
    expect(vcp).toHaveTextContent("-5.15%");
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

  it("shows the evening run while it is going, and nothing once it has finished", async () => {
    const run = { state: "running", started: "2026-10-13T19:15:02+05:30", step: "corporate actions", step_since: "19:16", exit_code: null };
    mockApi({ status: { ...fx.status, run } });
    renderApp(<StatusBar />);
    expect(await screen.findByTestId("run-progress")).toHaveTextContent("Daily run in progress: corporate actions since 19:16 (started 19:15 IST)");
  });

  it("says nothing about a finished run", async () => {
    const run = { state: "finished", started: "2026-10-13T19:15:02+05:30", step: "serving copy", step_since: "19:54", exit_code: 0 };
    mockApi({ status: { ...fx.status, run } });
    renderApp(<StatusBar />);
    expect(await screen.findByText("No warnings")).toBeInTheDocument();
    expect(screen.queryByTestId("run-progress")).not.toBeInTheDocument();
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
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument(); // an icon until it is clicked
    await userEvent.click(screen.getByRole("button", { name: "Search symbol or company" }));
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
    await userEvent.click(screen.getByRole("button", { name: "Search symbol or company" }));
    await userEvent.type(screen.getByRole("searchbox"), "zzz");
    expect(await screen.findByText("No match")).toBeInTheDocument();
  });
});

describe("score gauge", () => {
  const tip = () => screen.getByTestId("mhs-needle").getAttribute("points")?.split(" ")[0]?.split(",").map(Number) ?? [];
  it("points the needle at the raw score: left at 0, up at 50, right at 100", () => {
    const { rerender } = renderApp(<ScoreGauge raw={0} label="Adverse" />);
    expect(tip()[0]).toBeLessThan(40);
    expect(tip()[1]).toBeCloseTo(100, 0);
    rerender(<ScoreGauge raw={50} label="Mixed" />);
    expect(tip()[0]).toBeCloseTo(100, 0);
    expect(tip()[1]).toBeLessThan(40);
    rerender(<ScoreGauge raw={100} label="Strong" />);
    expect(tip()[0]).toBeGreaterThan(160);
    expect(screen.getByTestId("mhs-label")).toHaveTextContent("Strong");
    expect(screen.getByTestId("mhs-raw")).toHaveTextContent("100");
  });

  it("without a score: no needle, a dash", () => {
    renderApp(<ScoreGauge raw={null} label={null} />);
    expect(screen.queryByTestId("mhs-needle")).not.toBeInTheDocument();
    expect(screen.getByTestId("mhs-raw")).toHaveTextContent("—");
    expect(screen.getByTestId("mhs-label")).toHaveTextContent("No reading");
  });
});
