"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { generateReport, useReports, useStatus } from "@/lib/api";
import { fmtInt, fmtStamp } from "@/lib/fmt";
import type { ReportFile } from "@/lib/schemas";
import { Page } from "./Page";
import { FIELD } from "./Screener";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const url = (f: ReportFile): string => `/api/v1/reports/${f.kind}/${encodeURIComponent(f.name)}`;
const key = (f: ReportFile): string => `${f.kind}/${f.name}`;

function Group({ title, files, picked, onPick }: { title: string; files: ReportFile[]; picked: string; onPick: (k: string) => void }) {
  return (
    <section aria-label={title}>
      <h3 className="mb-1 text-[11px] uppercase text-mute">{title}</h3>
      {files.length === 0 ? (
        <p className="text-xs text-mute">None yet.</p>
      ) : (
        <ul className="space-y-1">
          {files.map((f) => (
            <li key={key(f)}>
              <button
                type="button"
                onClick={() => onPick(key(f))}
                aria-current={picked === key(f) ? "true" : undefined}
                className={`w-full rounded border px-2 py-1.5 text-left text-xs ${picked === key(f) ? "border-accent bg-accent/10 text-ink" : "border-line text-mute hover:text-ink"}`}
              >
                {f.label}
                <span className="block text-[10px] text-mute">{`written ${fmtStamp(f.modified)} · ${fmtInt(Math.max(1, Math.round(f.size_bytes / 1024)))} KB`}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Build the report of a past day or week, in one slim bar. It only writes an HTML file. */
function Generate({ onDone }: { onDone: (k: string) => void }) {
  const status = useStatus();
  const client = useQueryClient();
  const latest = status.data?.prices_date ?? undefined;
  const [kind, setKind] = useState<"daily" | "weekly">("daily");
  const [date, setDate] = useState("");
  const make = useMutation({
    mutationFn: () => generateReport(kind, date || latest || ""),
    onSuccess: async (f) => {
      await client.invalidateQueries({ queryKey: ["reports"] });
      onDone(key(f));
    },
  });
  const small = `${FIELD} !py-1`;
  return (
    <section aria-label="Generate a report" className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border border-line bg-panel px-4 py-2">
      <h2 className="text-sm font-semibold text-ink">Generate a report</h2>
      <form
        className="flex flex-wrap items-center gap-3 text-[11px] uppercase text-mute"
        onSubmit={(e) => {
          e.preventDefault();
          make.mutate();
        }}
      >
        <label className="flex items-center gap-2" htmlFor="gen-kind">
          Report
          <select id="gen-kind" value={kind} onChange={(e) => setKind(e.target.value as "daily" | "weekly")} className={small}>
            <option value="daily">Daily report</option>
            <option value="weekly">Weekly summary</option>
          </select>
        </label>
        <label className="flex items-center gap-2" htmlFor="gen-date">
          {kind === "daily" ? "Trading day" : "Any day of the week"}
          <input id="gen-date" type="date" max={latest} value={date || latest || ""} onChange={(e) => setDate(e.target.value)} className={small} />
        </label>
        <button
          type="submit"
          disabled={make.isPending || !(date || latest)}
          className="rounded border border-accent px-3 py-1 text-xs normal-case text-accent enabled:hover:bg-accent/10 disabled:opacity-40"
        >
          {make.isPending ? "Generating…" : "Generate"}
        </button>
        {make.isError ? (
          <span role="alert" className="text-xs normal-case text-down">
            {make.error instanceof Error ? make.error.message : "The report could not be made."}
          </span>
        ) : null}
        {make.isSuccess ? (
          <span role="status" className="text-xs normal-case text-up">{`Written: ${make.data.label}`}</span>
        ) : null}
      </form>
    </section>
  );
}

/** The daily and weekly HTML reports. On a wide screen the page fits the window: the list of
 *  reports and the report each scroll on their own. */
export function Reports() {
  const reports = useReports();
  const [chosen, setChosen] = useState<string | null>(null);
  const files = reports.data?.files ?? [];
  const daily = files.filter((f) => f.kind === "daily");
  const weekly = files.filter((f) => f.kind === "weekly");
  const picked = chosen ?? (daily[0] ? key(daily[0]) : weekly[0] ? key(weekly[0]) : "");
  const file = files.find((f) => key(f) === picked);

  return (
    <Page active="Reports" title="Reports" fill>
      <Generate onDone={setChosen} />
      <Card label="Reports" className="min-h-0 lg:flex-1">
        {reports.isError ? (
          <ErrorBox error={reports.error} />
        ) : reports.isPending ? (
          <Loading what="reports" />
        ) : files.length === 0 ? (
          <Empty>No report has been written yet. The daily run writes one; to write it now run: vcp report daily</Empty>
        ) : (
          <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
            <div className="max-h-60 space-y-4 overflow-y-auto pr-1 lg:max-h-none">
              <Group title="Daily" files={daily} picked={picked} onPick={setChosen} />
              <Group title="Weekly" files={weekly} picked={picked} onPick={setChosen} />
            </div>
            <div className="flex min-h-0 flex-col">
              {file ? (
                <>
                  <div className="mb-2 flex shrink-0 items-center justify-between gap-3 text-xs">
                    <span className="font-medium text-ink">{file.label}</span>
                    <a href={url(file)} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                      Open in a new tab
                    </a>
                  </div>
                  {/* the report is a plain page; no scripts are allowed in it */}
                  <iframe title={file.label} src={url(file)} sandbox="" className="h-[70vh] min-h-0 w-full rounded border border-line bg-white lg:h-auto lg:flex-1" />
                </>
              ) : null}
            </div>
          </div>
        )}
      </Card>
    </Page>
  );
}
