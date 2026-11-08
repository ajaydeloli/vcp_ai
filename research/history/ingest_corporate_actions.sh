#!/bin/bash
# Corporate actions of 2011-06 .. 2021-06 into the history research database, one calendar
# year per run (NSE only: an empty env file keeps the Upstox key out). Re-running is safe:
# actions have deterministic IDs. Owner request 2026-11-08 (history extension, stage 2).
set -u
cd "$(dirname "$0")/../.."
DB=data/history_2011.duckdb
ENV=/home/ubuntu/.cache/empty.env
touch "$ENV"
for y in 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021; do
  start="$y-01-01"; end="$y-12-31"
  [ "$y" = 2011 ] && start=2011-06-01
  [ "$y" = 2021 ] && end=2021-06-30
  log=data/logs/history_ca_$y.log
  echo "$(date -u +%FT%TZ) $y $start..$end" >> data/logs/history_ca_all.log
  .venv/bin/vcp ingest corporate-actions --start "$start" --end "$end" --db "$DB" \
    --env-file "$ENV" --all-records > "$log" 2>&1
  code=$?
  echo "$(date -u +%FT%TZ) $y exit=$code $(grep -h 'Reconciliation complete\|Fetched' "$log" | sed 's/.*INFO  //' | tr '\n' ' ')" >> data/logs/history_ca_all.log
  [ $code -ne 0 ] && { echo "STOPPED at $y" >> data/logs/history_ca_all.log; exit $code; }
done
echo "DONE" >> data/logs/history_ca_all.log
