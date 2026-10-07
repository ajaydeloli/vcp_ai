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
      expect(JSON.stringify(calls)).toContain("screener?strategy=vcp&min_rs=80&sort=rs_rank&direction=desc&page=1&page_size=25&stage=STAGE_2"),
    );
    expect(screen.getByRole("button", { name: "Stage 2, RS 80+" })).toHaveAttribute("aria-pressed", "true");
  });

  it("a setup preset and the strategy choice reach the API; the watch list's old lists are presets", async () => {
    const calls = mockApi();
    renderApp(<Screener />);
    await screen.findByRole("link", { name: "ALPHA" });
    await userEvent.selectOptions(screen.getByLabelText("Strategy"), "flat_base");
    await userEvent.click(screen.getByRole("button", { name: "A+ VCP" }));
    await waitFor(() => expect(calls.some((c) => c.includes("strategy=flat_base") && c.includes("classification=A_PLUS_VCP") && c.includes("has_setup=true"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "On several strategies" }));
    await waitFor(() => expect(calls.some((c) => c.includes("min_strategies=2"))).toBe(true));
    for (const name of ["Top setups", "VCP", "VCP like", "Forming", "Pivot ready", "Breakouts"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("builds the query string from the filters", () => {
    const q = presetQuery(PRESETS.find((p) => p.id === "tt")!, "alp");
    expect(paths.screener(q)).toBe("screener?strategy=vcp&q=alp&tt_pass=true&sort=rs_rank&direction=desc&page=1&page_size=25");
    expect(paths.screener(DEFAULT_QUERY)).toBe("screener?strategy=vcp&sort=rs_rank&direction=desc&page=1&page_size=25");
  });
});
