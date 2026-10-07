import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Screener } from "@/components/Screener";
import { paths } from "@/lib/api";
import { DEFAULT_QUERY, PRESETS, presetQuery } from "@/lib/screener";
import { mockApi, renderApp } from "./helpers";

describe("screener page", () => {
  it("lists every scanned stock with a link to its stock analysis page", async () => {
    mockApi();
    renderApp(<Screener />);
    expect(await screen.findByText(/stocks match/)).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: "ALPHA" });
    expect(link).toHaveAttribute("href", "/stocks/ALPHA");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Screener" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText(/not buy signals/)).toBeInTheDocument();
  });

  it("a preset selects its filters and asks the API for them", async () => {
    const calls = mockApi();
    renderApp(<Screener />);
    await screen.findByRole("link", { name: "ALPHA" });
    await userEvent.click(screen.getByRole("button", { name: "Stage 2, RS 80+" }));
    await waitFor(() =>
      expect(JSON.stringify(calls)).toContain("screener?min_rs=80&sort=rs_rank&direction=desc&page=1&page_size=25&stage=STAGE_2"),
    );
    expect(screen.getByRole("button", { name: "Stage 2, RS 80+" })).toHaveAttribute("aria-pressed", "true");
  });

  it("builds the query string from the filters", () => {
    const q = presetQuery(PRESETS.find((p) => p.id === "tt")!, "alp");
    expect(paths.screener(q)).toBe("screener?q=alp&tt_pass=true&sort=rs_rank&direction=desc&page=1&page_size=25");
    expect(paths.screener(DEFAULT_QUERY)).toBe("screener?sort=rs_rank&direction=desc&page=1&page_size=25");
  });
});
