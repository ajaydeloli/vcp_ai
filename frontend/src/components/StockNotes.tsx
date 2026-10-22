"use client";

// My notes on a stock (owner, 2026-10-22), kept in this browser only. Dated entries, newest first;
// Ctrl+Enter adds; delete asks once. "Export all notes" saves every stock's notes as CSV (backup).
import { useState } from "react";
import { MAX_NOTE, useAllNotes, useStockNotes } from "@/lib/notes";
import { download, toCsv } from "@/lib/tableFiles";
import { Card, Empty } from "./ui";

const when = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
};

/** "📝 2 notes": next to the symbol in the stock header; a link to the notes on the stock page. */
export function NotesBadge({ symbol, onStockPage }: { symbol: string; onStockPage: boolean }) {
  const { notes } = useStockNotes(symbol);
  if (notes.length === 0) return null;
  return (
    <a
      href={onStockPage ? "#my-notes" : `/stocks/${encodeURIComponent(symbol)}#my-notes`}
      className="text-xs text-warn hover:underline"
      title={notes[0]?.text}
      data-testid="notes-badge"
    >
      📝 {notes.length} {notes.length === 1 ? "note" : "notes"}
    </a>
  );
}

function ExportAll() {
  const all = useAllNotes();
  const rows = Object.entries(all).flatMap(([sym, list]) => list.map((n) => [sym, when(n.at), n.text]));
  if (rows.length === 0) return null;
  const save = () => {
    rows.sort((a, b) => String(a[0]).localeCompare(String(b[0])));
    const stamp = new Date().toISOString().slice(0, 10);
    download(new Blob([toCsv(["Symbol", "Written (IST)", "Note"], rows)], { type: "text/csv;charset=utf-8" }), `stock_notes_${stamp}.csv`);
  };
  return (
    <button
      type="button"
      onClick={save}
      className="rounded border border-line px-2 py-0.5 text-xs text-mute hover:border-accent hover:text-ink"
      title="Every note on every stock, as CSV (a backup)"
    >
      Export all notes
    </button>
  );
}

export function StockNotes({ symbol }: { symbol: string }) {
  const { notes, add, remove } = useStockNotes(symbol);
  const [text, setText] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);
  const submit = () => {
    if (!text.trim()) return;
    add(text);
    setText("");
  };
  return (
    <div id="my-notes" className="scroll-mt-20">
      <Card title="My notes" subtitle={`Your own notes on ${symbol}, kept in this browser only.`} right={<ExportAll />}>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
          <label htmlFor="note-text" className="sr-only">
            New note on {symbol}
          </label>
          <textarea
            id="note-text"
            value={text}
            maxLength={MAX_NOTE}
            rows={2}
            placeholder="e.g. Results on 5 Nov · wait for the handle · stop below 640"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                submit();
              }
            }}
            className="min-h-[2.5rem] w-full flex-1 rounded border border-line bg-panel2 px-2 py-1 text-sm text-ink placeholder:text-mute focus:border-accent focus:outline-none"
          />
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim()}
            className="rounded bg-accent px-3 py-1 text-xs text-white disabled:opacity-40"
            title="Add the note (Ctrl+Enter)"
          >
            Add note
          </button>
        </div>
        {notes.length === 0 ? (
          <div className="mt-2">
            <Empty>No notes on {symbol} yet.</Empty>
          </div>
        ) : (
          <ul className="mt-3 divide-y divide-line" aria-label={`Notes on ${symbol}`}>
            {notes.map((n) => (
              <li key={n.id} className="flex items-start gap-3 py-2 text-sm">
                <span className="w-32 shrink-0 text-xs tabular-nums text-mute">{when(n.at)}</span>
                <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-ink">{n.text}</span>
                {confirm === n.id ? (
                  <span className="flex shrink-0 gap-1 text-xs">
                    <button type="button" onClick={() => remove(n.id)} className="rounded border border-down/60 px-1.5 text-down">
                      Delete
                    </button>
                    <button type="button" onClick={() => setConfirm(null)} className="rounded border border-line px-1.5 text-mute">
                      Keep
                    </button>
                  </span>
                ) : (
                  <button type="button" onClick={() => setConfirm(n.id)} className="shrink-0 text-xs text-mute hover:text-down" aria-label={`Delete the note of ${when(n.at)}`}>
                    ✕
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
