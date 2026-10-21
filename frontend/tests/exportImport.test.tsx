import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Screener } from "@/components/Screener";
import { Watchlist } from "@/components/Watchlist";
import { readBlob } from "@/lib/tableFiles";
import { parseState, WATCH_KEY } from "@/lib/watchlist";
import { mockApi, renderApp } from "./helpers";

const stored = () => parseState(window.localStorage.getItem(WATCH_KEY) ?? "");
const blobs: { blob: Blob; name: string }[] = [];

beforeEach(() => {
  window.localStorage.clear();
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

const csvFile = (name: string, text: string) => new File([text], name, { type: "text/csv" });

describe("screener export", () => {
  it("exports every matching row with the visible columns as CSV", async () => {
    const calls = mockApi();
    renderApp(<Screener />);
    await userEvent.click(await screen.findByRole("button", { name: "CSV" }));
    await waitFor(() => expect(blobs).toHaveLength(1));
    expect(blobs[0]?.name).toMatch(/^screener_vcp_.+\.csv$/);
    const text = await readBlob(blobs[0]!.blob, "text");
    expect(text.split("\r\n")[0]).toContain("Symbol,Company");
    expect(text).toContain("ALPHA");
    expect(calls.some((c) => c.startsWith("screener") && c.includes("page_size=100"))).toBe(true);
  });
});

describe("watch-list import and export", () => {
  it("previews a CSV, leaves out unknown symbols and duplicates, then adds to the list", async () => {
    mockApi();
    renderApp(<Watchlist />);
    await userEvent.upload(screen.getByTestId("watchlist-import-input"), csvFile("picks.csv", "Name,Ticker\nA,NSE:ALPHA\nB,beta.ns\nZ,ZZZQ\nA2,ALPHA\n"));
    const preview = await screen.findByTestId("import-preview");
    expect(preview).toHaveTextContent("2 NSE stocks found");
    expect(preview).toHaveTextContent("1 duplicates skipped");
    expect(screen.getByTestId("import-unknown")).toHaveTextContent("ZZZQ");
    await userEvent.click(screen.getByRole("button", { name: "Add 2" }));
    expect(stored().lists[0]?.symbols).toEqual(["ALPHA", "BETA"]);
    expect(await screen.findByText(/2 added to/)).toBeInTheDocument();
  });

  it("can make a new list named after the file", async () => {
    mockApi();
    renderApp(<Watchlist />);
    await userEvent.upload(screen.getByTestId("watchlist-import-input"), csvFile("my_picks.csv", "GAMMA\nALPHA\n"));
    await screen.findByTestId("import-preview");
    await userEvent.click(screen.getByRole("radio", { name: /New list/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add 2" }));
    const s = stored();
    expect(s.lists.map((l) => l.name)).toEqual(["My watch list", "my picks"]);
    expect(s.lists[1]?.symbols).toEqual(["GAMMA", "ALPHA"]);
    expect(s.active).toBe(s.lists[1]?.id);
  });

  it("refuses an old .xls file with a clear message", async () => {
    mockApi();
    renderApp(<Watchlist />);
    await userEvent.upload(screen.getByTestId("watchlist-import-input"), new File(["x"], "old.xls"));
    expect(await screen.findByText(/save it as .xlsx or .csv/)).toBeInTheDocument();
  });

  it("exports the active list as CSV", async () => {
    window.localStorage.setItem(WATCH_KEY, JSON.stringify({ lists: [{ id: "l1", name: "Swing ideas", symbols: ["ALPHA"] }], active: "l1" }));
    mockApi();
    renderApp(<Watchlist />);
    await userEvent.click(await screen.findByRole("button", { name: "CSV" }));
    await waitFor(() => expect(blobs).toHaveLength(1));
    expect(blobs[0]?.name).toMatch(/^watchlist_Swing_ideas_.+\.csv$/);
    expect(await readBlob(blobs[0]!.blob, "text")).toContain("ALPHA");
  });
});
