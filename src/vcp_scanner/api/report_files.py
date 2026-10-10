"""The daily and weekly HTML reports on disk (FRONTEND_SPECIFICATION 67.27): listed and served as
they are. Read-only; the names are checked so no other file can be reached."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from vcp_scanner.api import models as m
from vcp_scanner.data.providers._time import IST

KINDS = ("daily", "weekly")
NAME = {
    "daily": re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$"),
    "weekly": re.compile(r"^(\d{4}-W\d{2})\.html$"),
}


def _label(kind: str, stem: str) -> str:
    return f"Daily report {stem}" if kind == "daily" else f"Weekly summary {stem}"


def list_reports(folder: Path | None) -> m.ReportsResponse:
    files: list[m.ReportFile] = []
    for kind in KINDS:
        if folder is None:
            break
        for p in sorted((folder / kind).glob("*.html"), reverse=True):
            hit = NAME[kind].match(p.name)
            if hit is None or not p.is_file():
                continue
            st = p.stat()
            files.append(
                m.ReportFile(
                    kind=kind, name=p.name, label=_label(kind, hit.group(1)),
                    size_bytes=st.st_size, modified=datetime.fromtimestamp(st.st_mtime, IST),
                )
            )  # fmt: skip
    newest = max((f.modified for f in files), default=datetime.now(IST))
    days = [date.fromisoformat(f.name[:-5]) for f in files if f.kind == "daily"]
    return m.ReportsResponse(as_of=max(days, default=None), data_time=newest, files=files)


def read_report(folder: Path | None, kind: str, name: str) -> str | None:
    """The file's text, or None when there is no such report (also for any unexpected name)."""
    if folder is None or kind not in NAME or not NAME[kind].match(name):
        return None
    path = folder / kind / name
    return path.read_text(encoding="utf-8") if path.is_file() else None
