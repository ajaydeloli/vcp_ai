import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { StockNotes } from "@/components/StockNotes";
import { NOTES_KEY, parseNotes } from "@/lib/notes";
import { mockApi, renderApp } from "./helpers";

const stored = () => parseNotes(window.localStorage.getItem(NOTES_KEY) ?? "");

beforeEach(() => window.localStorage.clear());

describe("my notes on a stock", () => {
  it("adds a note (button or Ctrl+Enter) and shows it newest first", async () => {
    mockApi();
    renderApp(<StockNotes symbol="BETA" />);
    const box = await screen.findByLabelText("New note on BETA");
    await userEvent.type(box, "Results on 5 Nov");
    await userEvent.click(screen.getByRole("button", { name: "Add note" }));
    await userEvent.type(box, "Wait for the handle{Control>}{Enter}{/Control}");
    const list = screen.getByRole("list", { name: "Notes on BETA" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(stored().BETA?.map((n) => n.text)).toEqual(["Results on 5 Nov", "Wait for the handle"]);
    expect(box).toHaveValue("");
  });

  it("asks once before deleting, and keeps notes per stock", async () => {
    window.localStorage.setItem(
      NOTES_KEY,
      JSON.stringify({ BETA: [{ id: "a", at: "2026-10-20T05:00:00Z", text: "old idea" }], ALPHA: [{ id: "b", at: "2026-10-21T05:00:00Z", text: "other stock" }] }),
    );
    mockApi();
    renderApp(<StockNotes symbol="BETA" />);
    expect(await screen.findByText("old idea")).toBeInTheDocument();
    expect(screen.queryByText("other stock")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Delete the note of/ }));
    await userEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(stored().BETA).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /Delete the note of/ }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(stored().BETA).toBeUndefined();
    expect(stored().ALPHA).toHaveLength(1);
    expect(screen.getByText(/No notes on BETA yet/)).toBeInTheDocument();
  });

  it("reads damaged storage safely", () => {
    expect(parseNotes("not json")).toEqual({});
    expect(parseNotes(JSON.stringify({ X: [{ id: 1 }], y: [{ id: "a", at: "t", text: "ok" }] }))).toEqual({ Y: [{ id: "a", at: "t", text: "ok" }] });
  });
});
