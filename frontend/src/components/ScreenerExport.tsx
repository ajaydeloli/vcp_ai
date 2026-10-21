"use client";

import { useState } from "react";
import type { ScreenerQuery } from "@/lib/screener";
import { allScreenerRows, screenerTable } from "@/lib/screenerExport";
import { download, toCsv, toXlsx } from "@/lib/tableFiles";

const btn = "rounded border border-line px-2 py-0.5 text-xs text-mute hover:border-accent hover:text-ink disabled:opacity-50";

/** Export every row matching the filters (not just this page), with the visible columns, as CSV or Excel. */
export function ScreenerExport({ query, keys }: { query: ScreenerQuery; keys: string[] }) {
  const [busy, setBusy] = useState<"csv" | "xlsx" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(kind: "csv" | "xlsx") {
    setBusy(kind);
    setError(null);
    try {
      const { rows, asOf } = await allScreenerRows(query);
      const { header, body } = screenerTable(rows, keys);
      const name = `screener_${query.strategy}_${asOf ?? "latest"}`;
      if (kind === "csv") download(new Blob([toCsv(header, body)], { type: "text/csv;charset=utf-8" }), `${name}.csv`);
      else download(await toXlsx(header, body, `Screener ${asOf ?? ""}`.trim()), `${name}.xlsx`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <span className="inline-flex items-center gap-1.5" role="group" aria-label="Export results">
      <span className="text-xs text-mute">Export</span>
      <button type="button" className={btn} disabled={busy !== null} onClick={() => run("csv")} title="All matching stocks, visible columns, as CSV">
        {busy === "csv" ? "…" : "CSV"}
      </button>
      <button type="button" className={btn} disabled={busy !== null} onClick={() => run("xlsx")} title="All matching stocks, visible columns, as Excel (.xlsx)">
        {busy === "xlsx" ? "…" : "Excel"}
      </button>
      {error ? (
        <span role="alert" className="text-xs text-down">
          {error}
        </span>
      ) : null}
    </span>
  );
}
