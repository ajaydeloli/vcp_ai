import ExcelJS from "exceljs";
import { describe, expect, it } from "vitest";
import { screenerTable, symbolBatches } from "@/lib/screenerExport";
import { cleanSymbol, matchSymbols, parseCsv, readXlsx, toCsv } from "@/lib/tableFiles";
import { fx } from "./helpers";

describe("CSV", () => {
  it("writes a UTF-8 BOM, quotes what needs it and defuses formulas", () => {
    const text = toCsv(
      ["Symbol", "Company", "RS"],
      [
        ["M&M", 'Mahindra, "M&M"', 91],
        ["X", "=HYPERLINK(1)", null],
      ],
    );
    expect(text.startsWith("﻿")).toBe(true);
    expect(text).toContain('M&M,"Mahindra, ""M&M""",91\r\n');
    expect(text).toContain("X,'=HYPERLINK(1),\r\n");
  });

  it("reads quoted fields, CRLF, semicolons and a BOM back", () => {
    expect(parseCsv('﻿Symbol,Name\r\nM&M,"Mahindra, ""M&M"""\r\n\r\nTCS;Tata\n')).toEqual([
      ["Symbol", "Name"],
      ["M&M", 'Mahindra, "M&M"'],
      ["TCS", "Tata"],
    ]);
  });
});

describe("symbol import", () => {
  const known = new Set(["RELIANCE", "M&M", "BAJAJ-AUTO", "TCS"]);

  it("cleans exchange prefixes and suffixes", () => {
    expect(cleanSymbol(" nse:reliance ")).toBe("RELIANCE");
    expect(cleanSymbol("RELIANCE.NS")).toBe("RELIANCE");
    expect(cleanSymbol("TCS-EQ")).toBe("TCS");
  });

  it("finds the symbol column by its heading and maps TradingView names back", () => {
    const got = matchSymbols(
      [
        ["Name", "Ticker", "Price"],
        ["Reliance", "NSE:RELIANCE", "1"],
        ["Mahindra", "NSE:M_M", "2"],
        ["Bajaj", "BAJAJ_AUTO", "3"],
        ["Again", "reliance.ns", "4"],
        ["Typo", "RELIANS", "5"],
      ],
      known,
    );
    expect(got.column).toBe("Ticker");
    expect(got.matched).toEqual(["RELIANCE", "M&M", "BAJAJ-AUTO"]);
    expect(got.unknown).toEqual(["RELIANS"]);
    expect(got.duplicates).toBe(1);
  });

  it("uses the first column, with or without a heading", () => {
    expect(matchSymbols([["TCS"], ["RELIANCE"]], known).matched).toEqual(["TCS", "RELIANCE"]);
    expect(matchSymbols([["My stocks"], ["TCS"]], known)).toMatchObject({ matched: ["TCS"], unknown: [], column: "first column (My stocks)" });
  });

  it("reads the first sheet of an Excel file", async () => {
    const book = new ExcelJS.Workbook();
    const ws = book.addWorksheet("List");
    ws.addRow(["Symbol", "Qty"]);
    ws.addRow(["TCS", 5]);
    const buf = await book.xlsx.writeBuffer();
    expect(await readXlsx(buf as ArrayBuffer)).toEqual([
      ["Symbol", "Qty"],
      ["TCS", "5"],
    ]);
  });

  it("splits long symbol lists into batches the API accepts", () => {
    const many = Array.from({ length: 500 }, (_, i) => `SYM${i}`);
    const batches = symbolBatches(many);
    expect(batches.flat()).toEqual(many);
    expect(Math.max(...batches.map((b) => b.join(",").length))).toBeLessThanOrEqual(1800);
  });
});

describe("screener export table", () => {
  it("has plain values for the visible columns only", () => {
    const { header, body } = screenerTable(fx.screener.rows, ["no", "watch", "symbol", "rs_rank", "live", "change_pct"]);
    expect(header).toEqual(["Symbol", "Company", "RS rank", "Change %"]);
    expect(body).toHaveLength(fx.screener.rows.length);
    expect(body[0]?.[0]).toBe(fx.screener.rows[0]?.symbol);
    expect(typeof body[0]?.[2]).toBe("number");
  });
});
