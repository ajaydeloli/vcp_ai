"""How far back NSE's equity bhavcopy archive goes (owner, 2026-11-08). Read-only: one request
per year (the first working day found in early July), about one second apart; nothing is
stored. Uses the downloader's own URL rule (``nse_bhavcopy.bhavcopy_url``)."""

from __future__ import annotations

import io
import time
import zipfile
from datetime import date, timedelta

import requests

from vcp_scanner.data.providers.nse_bhavcopy import bhavcopy_url

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept": "*/*"})
for year in range(1994, 2021):
    found = None
    for k in range(8):  # 1..8 July: the first day that answers
        d = date(year, 7, 1) + timedelta(days=k)
        if d.weekday() >= 5:
            continue
        try:
            r = s.get(bhavcopy_url(d), timeout=20)
        except requests.RequestException as e:
            print(year, d, "error", type(e).__name__, flush=True)
            time.sleep(2)
            continue
        time.sleep(1.0)
        if r.status_code == 200 and r.content[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(r.content))
            text = z.read(z.namelist()[0]).decode("latin-1").splitlines()
            eq = sum(1 for line in text[1:] if line.split(",")[1:2] == ["EQ"])
            found = (d, len(text) - 1, eq, text[0][:80])
            break
        last = r.status_code
    if found:
        d, rows, eq, head = found
        print(f"{year}: {d} OK  rows {rows}, EQ series {eq}  | {head}", flush=True)
    else:
        print(f"{year}: nothing in 1-8 July (last HTTP {last})", flush=True)
