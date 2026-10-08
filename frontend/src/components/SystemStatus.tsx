"use client";

import { useActivity, useLiveStatus, useStatus } from "@/lib/api";
import { DASH, fmtDay, fmtNum, fmtStamp, strategyLabel } from "@/lib/fmt";
import { feedState } from "@/lib/live";
import { Page } from "./Page";
import { Card, Empty, ErrorBox, Loading, TINT } from "./ui";

function Row({ k, v }: { k: string; v: string }) {
  return (
    <li className="flex justify-between gap-3 py-2 text-xs">
      <span className="text-mute">{k}</span>
      <span className="text-right tabular-nums text-ink">{v}</span>
    </li>
  );
}

/** Where the data stands: dates, the daily run, backups, warnings and the live feed. */
export function SystemStatus() {
  const status = useStatus();
  const live = useLiveStatus();
  const activity = useActivity(7);
  const s = status.data;
  const feed = live.data?.feed;

  return (
    <Page active="System Status" title="System Status">
      {status.isError ? <ErrorBox error={status.error} /> : null}
      {status.isPending ? <Loading what="status" /> : null}
      {s ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Card title="Data" subtitle="End-of-day data used by every scan" box={TINT.blue}>
            <ul className="divide-y divide-line">
              <Row k="Prices to" v={fmtDay(s.prices_date)} />
              <Row k="Data copy made" v={fmtStamp(s.data_time)} />
              <Row k="Daily run" v={s.daily_run ?? DASH} />
            </ul>
          </Card>
          <Card title="Strategies" subtitle="Latest scan and paper ledger" box={TINT.green}>
            <ul className="divide-y divide-line">
              {s.strategies.map((x) => (
                <Row key={x.strategy_id} k={strategyLabel(x.strategy_id)} v={`scan ${fmtDay(x.latest_scan)} · ledger ${fmtDay(x.ledger_through)}`} />
              ))}
            </ul>
          </Card>
          <Card title="Backup" box={TINT.amber}>
            <ul className="divide-y divide-line">
              <Row k="Newest" v={s.newest_backup ?? DASH} />
              <Row k="Age (days)" v={fmtNum(s.backup_age_days, 1)} />
            </ul>
          </Card>
          <Card title="Live prices" subtitle="Display only. Never used by a scan." box={TINT.cyan}>
            <ul className="divide-y divide-line">
              <Row k="State" v={feedState(feed)} />
              <Row k="Provider" v={feed?.provider ?? DASH} />
              <Row k="Market" v={feed?.market ?? DASH} />
              <Row k="Stocks covered" v={feed?.stocks_covered != null ? `${feed.stocks_covered} of ${feed.stocks_total ?? DASH}` : DASH} />
              <Row k="Last update" v={fmtStamp(feed?.last_success_at)} />
            </ul>
            {feed?.message ? <p className="mt-2 text-xs text-mute">{feed.message}</p> : null}
          </Card>
          <Card title="Warnings" box={TINT.orange} className="md:col-span-2">
            {s.warnings.length === 0 ? (
              <p className="text-xs text-up">No warnings.</p>
            ) : (
              <ul className="list-disc space-y-1 pl-5 text-xs text-warn">
                {s.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      ) : null}

      <Card title="Recent activity" subtitle="Last 7 days" box={TINT.pink}>
        {activity.isError ? (
          <ErrorBox error={activity.error} />
        ) : activity.isPending ? (
          <Loading what="activity" />
        ) : activity.data.events.length === 0 ? (
          <Empty>No activity in the last 7 days.</Empty>
        ) : (
          <ul className="divide-y divide-line text-xs">
            {activity.data.events.map((e, i) => (
              <li key={i} className="flex gap-3 py-2">
                <span className="w-24 shrink-0 text-mute">{fmtDay(e.day)}</span>
                <span className="w-24 shrink-0 text-mute">{e.symbol ?? DASH}</span>
                <span className="text-ink">{e.text}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Page>
  );
}
