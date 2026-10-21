"use client";

import { useRef, useState } from "react";
import { DEFAULT_QUERY } from "@/lib/screener";
import { knownSymbols, rowsForSymbols, screenerTable } from "@/lib/screenerExport";
import { cleanSymbol, download, matchSymbols, readTableFile, type SymbolMatch, toCsv, toXlsx } from "@/lib/tableFiles";
import { useWatchlists } from "@/lib/watchlist";

const btn = "rounded border border-line px-2 py-0.5 text-xs text-mute hover:border-accent hover:text-ink disabled:opacity-50";
const EXPORT_KEYS = ["symbol", "sector", "stage", "conditions_passed", "rs_rank", "close", "change_pct", "grade", "score", "pivot", "status", "pivot_distance_pct"];

type Preview = SymbolMatch & { file: string };

const baseName = (name: string) =>
  name
    .replace(/\.[^.]+$/, "")
    .replace(/[_-]+/g, " ")
    .trim();
const safeName = (name: string) => name.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "") || "watchlist";

/** Import symbols from a CSV or Excel file into a watch list, and export the active list. All in this browser. */
export function WatchlistFiles() {
  const { active, addMany, createWith } = useWatchlists();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [target, setTarget] = useState<"active" | "new">("active");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function pick(file: File) {
    setBusy("import");
    setMessage(null);
    setPreview(null);
    try {
      const rows = await readTableFile(file);
      const cells = rows
        .flatMap((r) => r.slice(0, 8))
        .map(cleanSymbol)
        .filter((x) => x.length > 0 && x.length <= 20);
      const candidates = cells.flatMap((x) => [x, x.replace(/_/g, "&"), x.replace(/_/g, "-")]);
      const known = await knownSymbols(candidates);
      const match = matchSymbols(rows, known);
      if (match.matched.length === 0 && match.unknown.length === 0) setMessage({ ok: false, text: `No symbols found in ${file.name}.` });
      else {
        setPreview({ ...match, file: file.name });
        setTarget("active");
      }
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "The file could not be read." });
    } finally {
      setBusy(null);
      if (input.current) input.current.value = "";
    }
  }

  function confirm() {
    if (!preview) return;
    if (target === "new") {
      createWith(baseName(preview.file), preview.matched);
      setMessage({ ok: true, text: `New list "${baseName(preview.file).slice(0, 40)}" with ${preview.matched.length} stocks.` });
    } else {
      const added = addMany(active.id, preview.matched);
      const already = preview.matched.length - added;
      setMessage({ ok: true, text: `${added} added to "${active.name}"${already ? `; ${already} were already in it` : ""}.` });
    }
    setPreview(null);
  }

  async function exportList(kind: "csv" | "xlsx") {
    setBusy(kind);
    setMessage(null);
    try {
      const { rows, asOf } = await rowsForSymbols(DEFAULT_QUERY, active.symbols);
      const { header, body } = screenerTable(rows, EXPORT_KEYS);
      const found = new Set(rows.map((r) => r.symbol));
      for (const s of active.symbols) if (!found.has(s)) body.push([s, ...header.slice(1).map(() => null)]);
      const name = `watchlist_${safeName(active.name)}_${asOf ?? "latest"}`;
      if (kind === "csv") download(new Blob([toCsv(header, body)], { type: "text/csv;charset=utf-8" }), `${name}.csv`);
      else download(await toXlsx(header, body, active.name), `${name}.xlsx`);
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Export failed" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <input
          ref={input}
          type="file"
          accept=".csv,.xlsx,.xls,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="hidden"
          aria-label="Import symbols from a CSV or Excel file"
          data-testid="watchlist-import-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void pick(f);
          }}
        />
        <button type="button" className={btn} disabled={busy !== null} onClick={() => input.current?.click()} title="Add symbols from a CSV or Excel (.xlsx) file">
          {busy === "import" ? "Reading…" : "Import"}
        </button>
        <span className="ml-1 text-xs text-mute">Export</span>
        <button type="button" className={btn} disabled={busy !== null || active.symbols.length === 0} onClick={() => exportList("csv")} title={`"${active.name}" as CSV`}>
          {busy === "csv" ? "…" : "CSV"}
        </button>
        <button
          type="button"
          className={btn}
          disabled={busy !== null || active.symbols.length === 0}
          onClick={() => exportList("xlsx")}
          title={`"${active.name}" as Excel (.xlsx)`}
        >
          {busy === "xlsx" ? "…" : "Excel"}
        </button>
      </div>
      {preview ? (
        <div role="dialog" aria-label="Import preview" className="rounded border border-line bg-panel p-3 text-xs text-ink" data-testid="import-preview">
          <p>
            <span className="font-semibold">{preview.file}</span>
            {preview.column ? <span className="text-mute"> · symbols from {preview.column}</span> : null}
          </p>
          <p className="mt-1">
            <span className="text-up">{preview.matched.length} NSE stocks found</span>
            {preview.duplicates ? <span className="text-mute"> · {preview.duplicates} duplicates skipped</span> : null}
          </p>
          {preview.unknown.length ? (
            <p className="mt-1 text-warn" data-testid="import-unknown">
              Not recognised ({preview.unknown.length}, left out): {preview.unknown.slice(0, 20).join(", ")}
              {preview.unknown.length > 20 ? " …" : ""}
            </p>
          ) : null}
          {preview.matched.length ? (
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <label className="inline-flex items-center gap-1">
                <input type="radio" name="import-target" checked={target === "active"} onChange={() => setTarget("active")} />
                Add to &quot;{active.name}&quot;
              </label>
              <label className="inline-flex items-center gap-1">
                <input type="radio" name="import-target" checked={target === "new"} onChange={() => setTarget("new")} />
                New list &quot;{baseName(preview.file).slice(0, 40)}&quot;
              </label>
              <button type="button" className="rounded bg-accent px-2 py-0.5 text-white" onClick={confirm}>
                Add {preview.matched.length}
              </button>
              <button type="button" className={btn} onClick={() => setPreview(null)}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="button" className={`${btn} mt-2`} onClick={() => setPreview(null)}>
              Close
            </button>
          )}
        </div>
      ) : null}
      {message ? (
        <p role="status" className={`text-xs ${message.ok ? "text-up" : "text-down"}`}>
          {message.text}
        </p>
      ) : null}
    </div>
  );
}
