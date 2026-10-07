import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { Screener } from "@/components/Screener";
import { Watchlist } from "@/components/Watchlist";
import { DEFAULT_NAME, parseState, WATCH_KEY } from "@/lib/watchlist";
import { mockApi, renderApp } from "./helpers";

const stored = () => parseState(window.localStorage.getItem(WATCH_KEY) ?? "");

beforeEach(() => window.localStorage.clear());

describe("my watch lists", () => {
  it("start with one empty list and say how to fill it", () => {
    mockApi();
    renderApp(<Watchlist />);
    expect(screen.getByRole("tab", { name: `${DEFAULT_NAME} (0)` })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/This list is empty/)).toBeInTheDocument();
  });

  it("a star on the screener puts a stock into a list; the page shows it with its scan result", async () => {
    mockApi();
    const first = renderApp(<Screener />);
    await userEvent.click(await screen.findByRole("button", { name: "Watch lists of ALPHA" }));
    await userEvent.click(screen.getByRole("checkbox", { name: DEFAULT_NAME }));
    expect(stored().lists[0]?.symbols).toEqual(["ALPHA"]);
    first.unmount();

    const calls = mockApi();
    renderApp(<Watchlist />);
    expect(await screen.findByRole("link", { name: "ALPHA" })).toHaveAttribute("href", "/stocks/ALPHA");
    expect(calls.some((c) => c.includes("symbols=ALPHA"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: `Remove ALPHA from ${DEFAULT_NAME}` }));
    await waitFor(() => expect(screen.getByText(/This list is empty/)).toBeInTheDocument());
    expect(stored().lists[0]?.symbols).toEqual([]);
  });

  it("makes, renames and deletes lists, each with its own stocks", async () => {
    mockApi();
    renderApp(<Watchlist />);
    await userEvent.click(screen.getByRole("button", { name: "+ New watchlist" }));
    await userEvent.type(screen.getByLabelText("New list name"), "Near pivot{Enter}");
    const tab = await screen.findByRole("tab", { name: "Near pivot (0)" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(stored().lists.map((l) => l.name)).toEqual([DEFAULT_NAME, "Near pivot"]);

    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const name = screen.getByLabelText("List name");
    await userEvent.clear(name);
    await userEvent.type(name, "Breakouts{Enter}");
    expect(await screen.findByRole("tab", { name: "Breakouts (0)" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete list" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(stored().lists.map((l) => l.name)).toEqual([DEFAULT_NAME]));
    expect(within(screen.getByRole("tablist")).getAllByRole("tab")).toHaveLength(1);
  });

  it("deleting the last list leaves one empty default list", async () => {
    mockApi();
    renderApp(<Watchlist />);
    await userEvent.click(screen.getByRole("button", { name: "Delete list" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByRole("tab", { name: `${DEFAULT_NAME} (0)` })).toBeInTheDocument();
  });

  it("reads a damaged store as the default list and keeps the first design's single list", () => {
    expect(parseState("{not json").lists.map((l) => l.name)).toEqual([DEFAULT_NAME]);
    expect(parseState('{"lists":[{"id":"a","name":"X","symbols":["A","A",3]}],"active":"zzz"}')).toEqual({
      lists: [{ id: "a", name: "X", symbols: ["A"] }],
      active: "a",
    });
    expect(parseState("", '["GLAND","KMEW"]').lists[0]?.symbols).toEqual(["GLAND", "KMEW"]);
  });
});
