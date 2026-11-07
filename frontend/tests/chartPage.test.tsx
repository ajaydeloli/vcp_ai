import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ChartPage } from "@/components/ChartPage";
import { CHART_KEY } from "@/lib/chartSettings";
import { WATCH_KEY } from "@/lib/watchlist";
import { liveCharts, type FakeChart } from "./chartMock";
import { fx, mockApi, renderApp } from "./helpers";

const chart = (): FakeChart => liveCharts()[liveCharts().length - 1]!;
const candles = () => chart().of("candles")[0]!;
const byScale = (id: string) => chart().series.filter((s) => s.options.priceScaleId === id);

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("chart page", () => {
  it("charts the stock full size with the default indicators, the legend and the VCP panel", async () => {
    const calls = mockApi();
    renderApp(<ChartPage symbol="ALPHA" />);
    await waitFor(() => expect(candles().data).toHaveLength(fx.bars.bars.length));
    expect(calls.some((c) => c.startsWith("stocks/ALPHA/bars?days=5000"))).toBe(true);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Chart" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Daily" })).toHaveAttribute("aria-pressed", "true");
    // the chosen interval and the Log button are filled with the accent colour, the others not
    expect(screen.getByRole("button", { name: "Daily" }).className).toMatch(/\bbg-accent\b/);
    expect(screen.getByRole("button", { name: "Daily" }).className).not.toMatch(/bg-panel2/);
    expect(screen.getByRole("button", { name: "Weekly" }).className).not.toMatch(/\bbg-accent\b/);
    expect(screen.getByRole("button", { name: "Log" }).className).not.toMatch(/\bbg-accent\b/);
    await userEvent.click(screen.getByRole("button", { name: "Log" }));
    expect(screen.getByRole("button", { name: "Log" }).className).toMatch(/\bbg-accent\b/);
    expect(screen.getByRole("button", { name: "Log" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Log" }));
    expect(screen.getByRole("button", { name: /Indicators \(8\)/ })).toBeInTheDocument();
    // the RS line against the Nifty 500 (the default benchmark), in its own pane
    await waitFor(() => expect(byScale("rs")[0]!.data.length).toBeGreaterThan(0));
    expect(calls.some((c) => c === "indices/closes?name=Nifty%20500")).toBe(true);
    expect(screen.getByTestId("chart-legend")).toHaveTextContent("RS vs Nifty 500");
    // today's RS Rating at the line's end, and in the legend
    await waitFor(() => expect(byScale("rs")[0]!.markers.map((mk) => mk.text)).toContain("RS Rating 91"));
    expect(screen.getByTestId("chart-legend")).toHaveTextContent("RS Rating 91");
    expect(calls).toContain("stocks/ALPHA/rs-ranks");
    const legend = screen.getByTestId("chart-legend");
    expect(legend).toHaveTextContent("ALPHA · 1D · NSE");
    expect(legend).toHaveTextContent(fx.bars.bars[fx.bars.bars.length - 1]!.day);
    expect(legend).toHaveTextContent("SMA 50");
    // VCP marks on the candles and the pivot / stop lines
    expect(candles().priceLines.map((l) => l.title)).toEqual(["Pivot", "Stop"]);
    const side = await screen.findByRole("complementary", { name: "VCP setup of ALPHA" });
    expect(within(side).getByText("Pivot").nextElementSibling).toHaveTextContent("120.00");
    expect(byScale("vol").find((s) => s.kind === "histogram")!.options.visible).not.toBe(false);
  });

  it("switches to weekly bars, OHLC bars and adds RSI; the settings are kept", async () => {
    mockApi();
    renderApp(<ChartPage symbol="ALPHA" />);
    await waitFor(() => expect(candles().data).toHaveLength(fx.bars.bars.length));
    await userEvent.click(screen.getByRole("button", { name: "Weekly" }));
    await waitFor(() => expect(candles().data.length).toBeLessThan(15));
    expect(screen.getByTestId("chart-legend")).toHaveTextContent("ALPHA · 1W · NSE");
    await userEvent.selectOptions(screen.getByLabelText("Chart type"), "ohlc");
    await waitFor(() => expect(chart().of("bars")[0]!.options.visible).toBe(true));
    expect(candles().options.visible).toBe(false);
    expect(chart().of("bars")[0]!.priceLines.map((l) => l.title)).toEqual(["Pivot", "Stop"]);

    await userEvent.click(screen.getByRole("button", { name: /Indicators/ }));
    const menu = screen.getByRole("dialog", { name: "Indicators" });
    await userEvent.click(within(menu).getByLabelText("RSI"));
    const rsiLine = byScale("rsi").find((s) => s.kind === "line")!;
    await waitFor(() => expect(rsiLine.options.visible).toBe(true));
    expect(rsiLine.priceLines.map((l) => l.price)).toEqual([70, 30]);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Indicators" })).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("RS benchmark"), "Nifty 50");
    await waitFor(() => expect(screen.getByTestId("chart-legend")).toHaveTextContent("RS vs Nifty 50"));
    await userEvent.selectOptions(screen.getByLabelText("Range"), "5Y");
    await userEvent.click(screen.getByRole("button", { name: "Log" }));
    const saved = JSON.parse(window.localStorage.getItem(CHART_KEY) ?? "{}");
    expect(saved).toMatchObject({ interval: "W", type: "ohlc", range: "5Y", log: true, benchmark: "Nifty 50" });
    expect(saved.toggles).toContain("rsi");
  });

  it("the legend follows the crosshair", async () => {
    mockApi();
    renderApp(<ChartPage symbol="ALPHA" />);
    await waitFor(() => expect(candles().data).toHaveLength(fx.bars.bars.length));
    act(() => chart().hover(0));
    expect(screen.getByTestId("chart-legend")).toHaveTextContent(fx.bars.bars[0]!.day);
    act(() => chart().hover(undefined));
    expect(screen.getByTestId("chart-legend")).toHaveTextContent(fx.bars.bars[fx.bars.bars.length - 1]!.day);
  });

  it("without a symbol it opens the best VCP setup, and a pick from the side list charts that stock", async () => {
    mockApi();
    renderApp(<ChartPage symbol={null} />);
    await waitFor(() => expect(screen.getByTestId("chart-legend")).toHaveTextContent("ALPHA · 1D"));
    const lists = screen.getByRole("complementary", { name: "Stocks to chart" });
    await userEvent.click(within(lists).getByRole("button", { name: /BETA/ }));
    await waitFor(() => expect(screen.getByTestId("chart-legend")).toHaveTextContent("BETA · 1D"));
  });

  it("every indicator is configurable: a 20-bar volume average, a new EMA, RSI lines", async () => {
    mockApi();
    renderApp(<ChartPage symbol="ALPHA" />);
    await waitFor(() => expect(candles().data).toHaveLength(fx.bars.bars.length));
    const legend = screen.getByTestId("chart-legend");
    expect(legend).toHaveTextContent("Vol SMA 50");
    await userEvent.click(screen.getByRole("button", { name: /Indicators/ }));
    const menu = screen.getByRole("dialog", { name: "Indicators" });
    const volma = byScale("vol").find((s) => s.kind === "line")!;
    const before = volma.data.length;
    const period = within(menu).getByLabelText("Volume average period");
    await userEvent.clear(period);
    await userEvent.type(period, "20");
    await waitFor(() => expect(legend).toHaveTextContent("Vol SMA 20"));
    expect(volma.data.length).toBe(before + 30); // a 20-bar average starts 30 bars sooner
    await userEvent.selectOptions(within(menu).getByLabelText("Volume average type"), "EMA");
    await waitFor(() => expect(legend).toHaveTextContent("Vol EMA 20"));

    // a new moving average, set to EMA 13 in a colour of our choice
    const lines = chart().of("line").length;
    await userEvent.click(within(menu).getByRole("button", { name: "+ Add moving average" }));
    await waitFor(() => expect(chart().of("line").length).toBe(lines + 1));
    const rows = within(menu).getAllByTestId("average-row");
    const n = rows.length;
    await userEvent.selectOptions(within(menu).getByLabelText(`Average ${n} type`), "EMA");
    const p = within(menu).getByLabelText(`Average ${n} period`);
    await userEvent.clear(p);
    await userEvent.type(p, "13");
    fireEvent.change(within(menu).getByLabelText(`Average ${n} colour`), { target: { value: "#123456" } });
    await waitFor(() => expect(legend).toHaveTextContent("EMA 13"));
    expect(chart().of("line").at(-1)!.options.color).toBe("#123456");
    await userEvent.click(within(menu).getByRole("button", { name: "Remove EMA 13" }));
    await waitFor(() => expect(chart().of("line").length).toBe(lines));
    expect(legend).not.toHaveTextContent("EMA 13");

    // RSI with its own lines
    await userEvent.click(within(menu).getByLabelText("RSI"));
    const upper = within(menu).getByLabelText("RSI upper line");
    await userEvent.clear(upper);
    await userEvent.type(upper, "80");
    const rsiLine = byScale("rsi").find((s) => s.kind === "line")!;
    await waitFor(() => expect(rsiLine.priceLines.map((l) => l.price)).toEqual([80, 30]));
    const saved = JSON.parse(window.localStorage.getItem(CHART_KEY) ?? "{}");
    expect(saved.params.volMa).toEqual({ kind: "EMA", period: 20 });
    expect(saved.params.rsi.upper).toBe(80);
  });

  it("the chart's colours can be changed and reset", async () => {
    mockApi();
    renderApp(<ChartPage symbol="ALPHA" />);
    await waitFor(() => expect(candles().data).toHaveLength(fx.bars.bars.length));
    await userEvent.click(screen.getByRole("button", { name: /Colours/ }));
    const menu = screen.getByRole("dialog", { name: "Colours" });
    fireEvent.change(within(menu).getByLabelText("Candle up colour"), { target: { value: "#0000ff" } });
    fireEvent.change(within(menu).getByLabelText("Wick down colour"), { target: { value: "#ffff00" } });
    fireEvent.change(within(menu).getByLabelText("Pivot line colour"), { target: { value: "#ff00ff" } });
    await waitFor(() => expect(candles().options.upColor).toBe("#0000ff"));
    expect(candles().options.wickDownColor).toBe("#ffff00");
    expect(candles().priceLines.find((l) => l.title === "Pivot")!.color).toBe("#ff00ff");
    expect(JSON.parse(window.localStorage.getItem(CHART_KEY) ?? "{}").colors.up).toBe("#0000ff");
    await userEvent.click(within(menu).getByRole("button", { name: "Reset colours" }));
    await waitFor(() => expect(candles().options.upColor).toBe("#26c281"));
  });

  it("the VCP panel opens by itself only when there is a setup, and a slim button shows or hides it", async () => {
    mockApi({ "stocks/NOSET/setups": { ...fx.stockSetups, symbol: "NOSET", setups: [] } });
    renderApp(<ChartPage symbol="NOSET" />);
    const show = await screen.findByRole("button", { name: "Show VCP analysis" });
    expect(show).toHaveTextContent("VCP (none)");
    expect(screen.queryByRole("complementary", { name: "VCP setup of NOSET" })).not.toBeInTheDocument();
    await userEvent.click(show);
    const panel = await screen.findByRole("complementary", { name: "VCP setup of NOSET" });
    expect(panel).toHaveTextContent("No VCP setup for NOSET");
    expect(JSON.parse(window.localStorage.getItem(CHART_KEY) ?? "{}").vcpPanel).toBe("open");
    await userEvent.click(screen.getByRole("button", { name: "Hide VCP analysis" }));
    expect(screen.queryByRole("complementary", { name: "VCP setup of NOSET" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show VCP analysis" })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(CHART_KEY) ?? "{}").vcpPanel).toBe("closed");
    // the stock lists are on the right, after the chart
    const lists = screen.getByRole("complementary", { name: "Stocks to chart" });
    const chartBox = screen.getByRole("region", { name: "Chart of NOSET" });
    expect(chartBox.compareDocumentPosition(lists) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("the right panel lists every watch list and the top setups, each opening and closing", async () => {
    window.localStorage.setItem(WATCH_KEY, JSON.stringify({
      lists: [
        { id: "a", name: "Leaders", symbols: ["ALPHA", "GAMMA"] },
        { id: "b", name: "Banks", symbols: ["BETA"] },
      ],
      active: "a",
    }));
    mockApi();
    renderApp(<ChartPage symbol="ALPHA" />);
    const lists = await screen.findByRole("complementary", { name: "Stocks to chart" });
    const leaders = within(lists).getByRole("button", { name: /Leaders/ });
    const banks = within(lists).getByRole("button", { name: /Banks/ });
    const top = within(lists).getByRole("button", { name: /Top VCP setups/ });
    // at first the active list and the setups are open
    expect(leaders).toHaveAttribute("aria-expanded", "true");
    expect(banks).toHaveAttribute("aria-expanded", "false");
    expect(top).toHaveAttribute("aria-expanded", "true");
    expect(leaders).toHaveTextContent("2");
    expect(await within(lists).findByRole("button", { name: /^BETA \d+$/ })).toBeInTheDocument(); // in the top setups, with its score
    await userEvent.click(top);
    expect(within(lists).queryByRole("button", { name: /^BETA/ })).not.toBeInTheDocument();
    await userEvent.click(banks);
    await userEvent.click(within(lists).getByRole("button", { name: "BETA" }));
    await waitFor(() => expect(screen.getByTestId("chart-legend")).toHaveTextContent("BETA · 1D"));
    expect(JSON.parse(window.localStorage.getItem("vcp-chart-lists-open") ?? "[]")).toEqual(["a", "b"]);
  });
});
