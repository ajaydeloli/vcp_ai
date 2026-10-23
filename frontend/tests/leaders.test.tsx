import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PastLeaders } from "@/components/PastLeaders";
import { baseSegments, filterLeaders, leaderMarkers, leadersTable, NO_FILTER } from "@/lib/leaders";
import type { LeaderRow } from "@/lib/schemas";
import { readBlob } from "@/lib/tableFiles";
import { liveCharts } from "./chartMock";
import { fx, mockApi, renderApp } from "./helpers";

const RUNS = fx.leaders.runs as LeaderRow[];
const blobs: { blob: Blob; name: string }[] = [];

beforeEach(() => {
  blobs.length = 0;
  let last: Blob | null = null;
  URL.createObjectURL = vi.fn((b: Blob) => {
    last = b;
    return "blob:test";
  }) as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    if (last) blobs.push({ blob: last, name: this.download });
  });
});
afterEach(() => vi.restoreAllMocks());

const symbols = () =>
  within(screen.getByRole("table"))
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell")[0]?.textContent);

describe("past leaders helpers", () => {
  it("filters by symbol, year, sector (Unclassified = none), band and VCP", () => {
    const syms = (f: Partial<typeof NO_FILTER>) => filterLeaders(RUNS, { ...NO_FILTER, ...f }).map((r) => r.symbol);
    expect(syms({})).toEqual(["BETA", "GAMMA", "ALPHA"]);
    expect(syms({ q: "alp" })).toEqual(["ALPHA"]);
    expect(syms({ year: "2024" })).toEqual(["GAMMA", "ALPHA"]);
    expect(syms({ sector: "Unclassified" })).toEqual(["GAMMA"]);
    expect(syms({ band: "tripled" })).toEqual(["GAMMA"]);
    expect(syms({ vcp: true })).toEqual(["ALPHA"]);
  });

  it("the export table has every column, numbers as numbers and no-scan spelt out", () => {
    const { header, body } = leadersTable(RUNS);
    expect(header[0]).toBe("Symbol");
    expect(header).toContain("VCP before breakout");
    expect(body).toHaveLength(3);
    const gamma = body[1]!;
    expect(gamma[0]).toBe("GAMMA");
    expect(gamma[1]).toBe("Unclassified");
    expect(gamma[header.indexOf("Gain %")]).toBe(230);
    expect(gamma[header.indexOf("VCP before breakout")]).toBe("No scan");
  });

  it("marks the launch, each breakout and the top, and draws each base up to its breakout", () => {
    const { run, bases, bars } = fx.leaderRun;
    const marks = leaderMarkers(run as LeaderRow, bases, bars);
    expect(marks.map((m) => m.text)).toEqual(["Launch", "B1", "B2", "Top"]);
    expect(marks[0]).toMatchObject({ time: "2024-03-01", position: "belowBar" });
    const segs = baseSegments(bases, bars);
    expect(segs).toHaveLength(2);
    expect(segs[0]!.points).toEqual([
      { time: "2024-02-01", value: 115 },
      { time: "2024-04-01", value: 115 },
    ]);
    expect(leaderMarkers(run as LeaderRow, bases, [])).toEqual([]); // no bar, no mark
  });
});

describe("past leaders page", () => {
  it("lists every run newest first, filters and sorts them", async () => {
    mockApi();
    renderApp(<PastLeaders />);
    expect(await screen.findByText(/3 runs, prices to/)).toBeInTheDocument();
    expect(symbols()).toEqual(["BETA", "GAMMA", "ALPHA"]);
    expect(screen.getByRole("link", { name: "ALPHA" })).toHaveAttribute("href", "/stocks/ALPHA");
    expect(screen.getByText("Survivorship partial.")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Gain"), "doubled");
    expect(symbols()).toEqual(["ALPHA"]);
    await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    await userEvent.click(screen.getByLabelText("VCP at launch"));
    expect(symbols()).toEqual(["ALPHA"]);
    await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    await userEvent.selectOptions(screen.getByLabelText("Sector"), "Unclassified");
    expect(symbols()).toEqual(["GAMMA"]);
    await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));

    await userEvent.click(screen.getByRole("button", { name: /^Gain/ }));
    expect(symbols()).toEqual(["GAMMA", "ALPHA", "BETA"]); // biggest first
    expect(screen.getByText(/\+65%\s*\*/)).toBeInTheDocument(); // BETA: the year is not over yet
  });

  it("exports the matching runs as CSV", async () => {
    mockApi();
    renderApp(<PastLeaders />);
    await screen.findByText(/3 runs, prices to/);
    await userEvent.selectOptions(screen.getByLabelText("Launch year"), "2024");
    await userEvent.click(screen.getByRole("button", { name: "CSV" }));
    await waitFor(() => expect(blobs).toHaveLength(1));
    expect(blobs[0]?.name).toBe("past_leaders.csv");
    const text = await readBlob(blobs[0]!.blob, "text");
    expect(text).toContain("GAMMA");
    expect(text).toContain("ALPHA");
    expect(text).not.toContain("BETA");
  });

  it("a run's Chart button shows its chart with the launch, bases and top", async () => {
    const calls = mockApi();
    renderApp(<PastLeaders />);
    await screen.findByText(/3 runs, prices to/);
    const button = screen.getByRole("button", { name: /Chart of ALPHA's run/ });
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText(/B1: .* above/)).toBeInTheDocument();
    expect(calls).toContain("leaders/1");
    await waitFor(() => expect(liveCharts().length).toBeGreaterThanOrEqual(1));
    const chart = liveCharts().at(-1)!;
    const candles = chart.series.find((s) => s.kind === "candles")!;
    expect(candles.markers.map((m: { text: string }) => m.text)).toEqual(["Launch", "B1", "B2", "Top"]);
    expect(chart.series.filter((s) => s.kind === "line")).toHaveLength(3); // the 50-day + two bases
    await userEvent.click(screen.getByRole("button", { name: "Close chart" }));
    expect(screen.queryByText(/B1: .* above/)).not.toBeInTheDocument();
  });

  it("says so when no study is stored", async () => {
    mockApi({ leaders: { ...fx.leaders, as_of: null, study: null, years: [], sectors: [], runs: [] } });
    renderApp(<PastLeaders />);
    expect(await screen.findByText(/No study stored yet/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
