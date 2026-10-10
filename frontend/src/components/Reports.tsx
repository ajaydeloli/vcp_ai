"use client";

import { useState } from "react";
import { useReports } from "@/lib/api";
import { fmtInt, fmtStamp } from "@/lib/fmt";
import type { ReportFile } from "@/lib/schemas";
import { Page } from "./Page";
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
        <ul className="max-h-64 space-y-1 overflow-y-auto">
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

/** The daily and weekly HTML reports written by the daily run, shown as they are. */
export function Reports() {
  const reports = useReports();
  const [chosen, setChosen] = useState<string | null>(null);
  const files = reports.data?.files ?? [];
  const daily = files.filter((f) => f.kind === "daily");
  const weekly = files.filter((f) => f.kind === "weekly");
  const picked = chosen ?? (daily[0] ? key(daily[0]) : weekly[0] ? key(weekly[0]) : "");
  const file = files.find((f) => key(f) === picked);

  return (
    <Page active="Reports" title="Reports">
      <Card
        title="Reports"
        subtitle="Written by the daily run after the serving copy: a daily report each session and a weekly summary on Fridays. Watch lists for paper monitoring, not trade instructions."
      >
        {reports.isError ? (
          <ErrorBox error={reports.error} />
        ) : reports.isPending ? (
          <Loading what="reports" />
        ) : files.length === 0 ? (
          <Empty>No report has been written yet. The daily run writes one; to write it now run: vcp report daily</Empty>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
            <div className="space-y-4">
              <Group title="Daily" files={daily} picked={picked} onPick={setChosen} />
              <Group title="Weekly" files={weekly} picked={picked} onPick={setChosen} />
            </div>
            <div>
              {file ? (
                <>
                  <div className="mb-2 flex items-center justify-between gap-3 text-xs">
                    <span className="font-medium text-ink">{file.label}</span>
                    <a href={url(file)} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                      Open in a new tab
                    </a>
                  </div>
                  {/* the report is a plain page; no scripts are allowed in it */}
                  <iframe title={file.label} src={url(file)} sandbox="" className="h-[75vh] w-full rounded border border-line bg-white" />
                </>
              ) : null}
            </div>
          </div>
        )}
      </Card>
    </Page>
  );
}
