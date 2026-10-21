// Tables to and from files, in the browser only (owner decision 2026-10-21): CSV and Excel
// (.xlsx) export, and reading a symbol list from a CSV or .xlsx file. Nothing is uploaded; ExcelJS
// is loaded only when an Excel file is written or read.

export type Cell = string | number | boolean | null | undefined;

/** A text cell that a spreadsheet would run as a formula gets a leading apostrophe. */
function safeText(s: string): string {
  return /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
}

function csvCell(v: Cell): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  const s = safeText(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV with a byte-order mark, so Excel reads the text as UTF-8. */
export function toCsv(header: string[], rows: Cell[][]): string {
  return "﻿" + [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

export async function toXlsx(header: string[], rows: Cell[][], sheet: string): Promise<Blob> {
  const ExcelJS = (await import("exceljs")).default;
  const book = new ExcelJS.Workbook();
  const ws = book.addWorksheet(sheet.slice(0, 31));
  ws.addRow(header).font = { bold: true };
  for (const r of rows) ws.addRow(r.map((v) => (v === undefined ? null : typeof v === "string" ? safeText(v) : v)));
  ws.views = [{ state: "frozen", ySplit: 1 }];
  header.forEach((h, i) => {
    const widest = Math.max(h.length, ...rows.slice(0, 200).map((r) => String(r[i] ?? "").length));
    ws.getColumn(i + 1).width = Math.min(40, Math.max(8, widest + 2));
  });
  const buf = await book.xlsx.writeBuffer();
  return new Blob([buf], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
}

export function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** RFC 4180 CSV (quoted fields, doubled quotes, CRLF or LF); a leading byte-order mark is dropped. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const s = text.replace(/^﻿/, "");
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (quoted) {
      if (ch === '"' && s[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === "," || ch === ";" || ch === "\t") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && s[i + 1] === "\n") i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else cell += ch;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

/** The first sheet of an .xlsx file as rows of text. */
export async function readXlsx(data: ArrayBuffer): Promise<string[][]> {
  const ExcelJS = (await import("exceljs")).default;
  const book = new ExcelJS.Workbook();
  await book.xlsx.load(data);
  const ws = book.worksheets[0];
  if (!ws) return [];
  const rows: string[][] = [];
  ws.eachRow({ includeEmpty: false }, (r) => {
    const out: string[] = [];
    r.eachCell({ includeEmpty: true }, (c, col) => {
      out[col - 1] = c.text ?? "";
    });
    rows.push(Array.from(out, (x) => x ?? ""));
  });
  return rows;
}

/** A file's contents through FileReader (works in every browser and in jsdom). */
export function readBlob(blob: Blob, as: "text"): Promise<string>;
export function readBlob(blob: Blob, as: "buffer"): Promise<ArrayBuffer>;
export function readBlob(blob: Blob, as: "text" | "buffer"): Promise<string | ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string | ArrayBuffer);
    r.onerror = () => reject(r.error ?? new Error("The file could not be read."));
    if (as === "text") r.readAsText(blob, "utf-8");
    else r.readAsArrayBuffer(blob);
  });
}

export async function readTableFile(file: File): Promise<string[][]> {
  const name = file.name.toLowerCase();
  if (name.endsWith(".xlsx")) return readXlsx(await readBlob(file, "buffer"));
  if (name.endsWith(".xls")) throw new Error("Old .xls files cannot be read: save it as .xlsx or .csv in Excel first.");
  return parseCsv(await readBlob(file, "text"));
}

const SYMBOL_HEADERS = [
  "symbol",
  "symbols",
  "ticker",
  "tickers",
  "stock",
  "stocks",
  "scrip",
  "scrip name",
  "nse symbol",
  "nse code",
  "trading symbol",
  "tradingsymbol",
  "instrument",
];

/** NSE:RELIANCE, RELIANCE.NS, " reliance " -> RELIANCE. */
export function cleanSymbol(raw: string): string {
  return raw
    .trim()
    .toUpperCase()
    .replace(/^NSE[:_]/, "")
    .replace(/[.-](NS|EQ)$/, "")
    .replace(/\s+/g, "");
}

export type SymbolMatch = { matched: string[]; unknown: string[]; duplicates: number; column: string };

/** Finds the symbol column (by its heading, else the first column) and matches it to known symbols.
 *  A TradingView-style "_" is tried as "&" and "-" (M_M -> M&M, BAJAJ_AUTO -> BAJAJ-AUTO). */
export function matchSymbols(rows: string[][], known: Set<string>): SymbolMatch {
  const top = rows[0];
  if (!top) return { matched: [], unknown: [], duplicates: 0, column: "" };
  const head = top.map((h) => h.trim().toLowerCase());
  let col = head.findIndex((h) => SYMBOL_HEADERS.includes(h));
  let body = rows.slice(1);
  let column = col >= 0 ? (top[col] ?? "").trim() : "";
  if (col < 0) {
    col = 0;
    const cell = (top[0] ?? "").trim();
    const first = cleanSymbol(cell);
    const isData = known.has(first) || known.has(first.replace(/_/g, "&")) || known.has(first.replace(/_/g, "-"));
    body = isData ? rows : rows.slice(1);
    column = isData ? "first column" : `first column (${cell || "no heading"})`;
  }
  const matched: string[] = [];
  const unknown: string[] = [];
  const seen = new Set<string>();
  let duplicates = 0;
  for (const r of body) {
    const raw = (r[col] ?? "").trim();
    if (!raw) continue;
    const s = cleanSymbol(raw);
    const hit = [s, s.replace(/_/g, "&"), s.replace(/_/g, "-")].find((x) => known.has(x));
    const key = hit ?? s;
    if (seen.has(key)) {
      duplicates++;
      continue;
    }
    seen.add(key);
    (hit ? matched : unknown).push(hit ?? raw);
  }
  return { matched, unknown, duplicates, column };
}
