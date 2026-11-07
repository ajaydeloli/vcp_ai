import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { HIDEABLE_COLS } from "@/components/Preferences";
import { COLS, Screener } from "@/components/Screener";
import { fmtInt, fmtPrice } from "@/lib/fmt";
import { DEFAULT_PREFS, parsePrefs, PREFS_KEY } from "@/lib/prefs";
import { resetNumberLocale } from "@/lib/numberStyle";
import { mockApi, renderApp } from "./helpers";

const stored = () => parsePrefs(window.localStorage.getItem(PREFS_KEY) ?? "");

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  resetNumberLocale();
});

describe("preferences", () => {
  it("the ⚙ button opens the panel; a theme choice is saved and applied at once; Escape closes it", async () => {
    mockApi();
    renderApp(<Screener />);
    await userEvent.click(screen.getByRole("button", { name: "Preferences" }));
    const panel = screen.getByRole("dialog", { name: "Preferences" });
    await userEvent.click(within(panel).getByText("Light"));
    expect(stored().theme).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    await userEvent.click(within(panel).getByText("Dark"));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Preferences" })).not.toBeInTheDocument();
  });

  it("hides a Screener column and resets to the defaults", async () => {
    mockApi();
    renderApp(<Screener />);
    expect(await screen.findByRole("columnheader", { name: /Pivot/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Preferences" }));
    const panel = screen.getByRole("dialog", { name: "Preferences" });
    await userEvent.click(within(panel).getByRole("checkbox", { name: "Pivot" }));
    expect(stored().hiddenCols).toEqual(["pivot"]);
    await waitFor(() => expect(screen.queryByRole("columnheader", { name: /^Pivot/ })).not.toBeInTheDocument());
    await userEvent.click(within(panel).getByRole("button", { name: "Reset to defaults" }));
    expect(stored()).toEqual(DEFAULT_PREFS);
  });

  it("rows per page is typed: any whole number from 5 to 100", async () => {
    const calls = mockApi();
    renderApp(<Screener />);
    await userEvent.click(screen.getByRole("button", { name: "Preferences" }));
    const box = within(screen.getByRole("dialog", { name: "Preferences" })).getByRole("spinbutton", { name: "Rows per page" });
    await userEvent.clear(box);
    await userEvent.type(box, "20");
    expect(stored().pageSize).toBe(20);
    await waitFor(() => expect(calls.some((c) => c.startsWith("screener") && c.includes("page_size=20"))).toBe(true));
    await userEvent.clear(box);
    await userEvent.type(box, "300{Enter}");
    expect(stored().pageSize).toBe(100);
    expect(box).toHaveValue(100);
    await userEvent.clear(box);
    await userEvent.type(box, "2");
    await userEvent.tab();
    expect(stored().pageSize).toBe(5);
  });

  it("the Screener opens with the preferred rows per page", async () => {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify({ pageSize: 50 }));
    const calls = mockApi();
    renderApp(<Screener />);
    await waitFor(() => expect(calls.some((c) => c.startsWith("screener") && c.includes("page_size=50"))).toBe(true));
  });

  it("formats numbers the Indian or the international way", () => {
    expect(fmtInt(1234567)).toBe("12,34,567");
    window.localStorage.setItem(PREFS_KEY, JSON.stringify({ numbers: "intl" }));
    resetNumberLocale();
    expect(fmtInt(1234567)).toBe("1,234,567");
    expect(fmtPrice(1234.5)).toBe("1,234.50");
  });

  it("reads damaged or unknown values as the defaults", () => {
    expect(parsePrefs("not json")).toEqual(DEFAULT_PREFS);
    expect(parsePrefs(JSON.stringify({ theme: "pink", pageSize: 500, startPage: "/evil", strategy: "Bad Name!" }))).toEqual(DEFAULT_PREFS);
  });

  it("every hideable column is a real Screener column", () => {
    const keys = new Set(COLS.map((c) => c.key));
    for (const [key] of HIDEABLE_COLS) expect(keys.has(key)).toBe(true);
  });
});
