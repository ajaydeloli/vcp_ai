import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { Watchlist } from "@/components/Watchlist";
import { Screener } from "@/components/Screener";
import { parseList, WATCH_KEY } from "@/lib/watchlist";
import { mockApi, renderApp } from "./helpers";

beforeEach(() => window.localStorage.clear());

describe("my watch list", () => {
  it("is empty at first and says how to fill it", () => {
    mockApi();
    renderApp(<Watchlist />);
    expect(screen.getByText(/Your list is empty/)).toBeInTheDocument();
  });

  it("a star on the screener adds the stock, and the watch list shows it with its scan result", async () => {
    mockApi();
    const first = renderApp(<Screener />);
    await userEvent.click(await screen.findByRole("button", { name: "Add ALPHA to my watch list" }));
    expect(parseList(window.localStorage.getItem(WATCH_KEY) ?? "")).toEqual(["ALPHA"]);
    first.unmount();

    const calls = mockApi();
    renderApp(<Watchlist />);
    expect(await screen.findByRole("link", { name: "ALPHA" })).toHaveAttribute("href", "/stocks/ALPHA");
    expect(calls.some((c) => c.includes("symbols=ALPHA"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Remove ALPHA from my watch list" }));
    await waitFor(() => expect(screen.getByText(/Your list is empty/)).toBeInTheDocument());
    expect(parseList(window.localStorage.getItem(WATCH_KEY) ?? "")).toEqual([]);
  });

  it("ignores a damaged stored list", () => {
    expect(parseList("{not json")).toEqual([]);
    expect(parseList('["A","A",3]')).toEqual(["A"]);
  });
});
