import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ChartPage } from "@/components/ChartPage";
import { CHART_KEY } from "@/lib/chartSettings";
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
    expect(screen.getByRole("button", { name: /Indicators \(7\)/ })).toBeInTheDocument();
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
    await userEvent.click(within(menu).getByLabelText(/RSI \(14\)/));
    const rsiLine = byScale("rsi").find((s) => s.kind === "line")!;
    await waitFor(() => expect(rsiLine.options.visible).toBe(true));
    expect(rsiLine.priceLines.map((l) => l.price)).toEqual([70, 30]);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Indicators" })).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Range"), "5Y");
    await userEvent.click(screen.getByRole("button", { name: "Log" }));
    const saved = JSON.parse(window.localStorage.getItem(CHART_KEY) ?? "{}");
    expect(saved).toMatchObject({ interval: "W", type: "ohlc", range: "5Y", log: true });
    expect(saved.indicators).toContain("rsi");
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
});
