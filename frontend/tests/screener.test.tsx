import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Screener } from "@/components/Screener";
import { paths } from "@/lib/api";
import { DEFAULT_QUERY } from "@/lib/screener";
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

  it("has no preset buttons; the filters sit above the results table", async () => {
    mockApi();
    renderApp(<Screener />);
    await screen.findByRole("link", { name: "ALPHA" });
    expect(screen.queryByRole("group", { name: "Presets" })).toBeNull();
    for (const name of ["All scanned stocks", "Top setups", "Pivot ready", "Breakouts", "On several strategies"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    const filters = screen.getByRole("heading", { name: "Filters" });
    const table = screen.getByRole("table");
    expect(filters.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("the filters reach the API", async () => {
    const calls = mockApi();
    renderApp(<Screener />);
    await screen.findByRole("link", { name: "ALPHA" });
    await userEvent.selectOptions(screen.getByLabelText("Strategy"), "flat_base");
    await userEvent.selectOptions(screen.getByLabelText("Setup class"), "A_PLUS_VCP");
    await userEvent.selectOptions(screen.getByLabelText("VCP setup"), "ranked");
    await waitFor(() => expect(calls.some((c) => c.includes("strategy=flat_base") && c.includes("classification=A_PLUS_VCP") && c.includes("has_setup=true"))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("On strategies"), "2");
    await waitFor(() => expect(calls.some((c) => c.includes("min_strategies=2"))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Trend Template"), "yes");
    await userEvent.click(screen.getByLabelText(/Stage 2/));
    await userEvent.type(screen.getByLabelText("Min RS rank"), "80");
    await waitFor(() => expect(calls.some((c) => c.includes("tt_pass=true") && c.includes("stage=STAGE_2") && c.includes("min_rs=80"))).toBe(true));
  });

  it("Reset filters clears them and is off when nothing is set", async () => {
    mockApi();
    renderApp(<Screener />);
    await screen.findByRole("link", { name: "ALPHA" });
    const reset = screen.getByRole("button", { name: "Reset filters" });
    expect(reset).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Trend Template"), "yes");
    expect(reset).toBeEnabled();
    await userEvent.click(reset);
    expect(screen.getByLabelText("Trend Template")).toHaveValue("");
    expect(reset).toBeDisabled();
  });

  it("builds the query string from the filters", () => {
    const q = { ...DEFAULT_QUERY, q: "alp", ttPass: true };
    expect(paths.screener(q)).toBe("screener?strategy=vcp&q=alp&tt_pass=true&sort=rs_rank&direction=desc&page=1&page_size=25");
    expect(paths.screener(DEFAULT_QUERY)).toBe("screener?strategy=vcp&sort=rs_rank&direction=desc&page=1&page_size=25");
  });
});
