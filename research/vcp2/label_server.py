"""Blind chart labelling for the VCP detector check (spec section 5; owner go 2026-11-10).

A small local tool, separate from the read-only dashboard: it serves one page and reads and
writes only the labels database built by ``label_sample.py``. Charts show no detector marks
and no bar after the scan day. The detectors' verdicts (table ``sample_truth``) are never sent
to the page.

    .venv/bin/python research/vcp2/label_server.py [--port 8010]
    then open http://localhost:8010
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LABELS = ROOT / "data" / "research" / "vcp2_labels.duckdb"
VERDICTS = ("yes", "no", "unsure")


class Mark(BaseModel):
    date: date
    price: float


class Label(BaseModel):
    verdict: str = Field(pattern="^(yes|no|unsure)$")
    t1: Mark | None = None
    lows: list[Mark] = []
    pivot: Mark | None = None
    note: str | None = Field(None, max_length=500)


def make_app(db: Path) -> FastAPI:
    app = FastAPI(title="VCP labelling")

    def con(read_only: bool = True) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(db), read_only=read_only)

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return (HERE / "label_page.html").read_text(encoding="utf-8")

    @app.get("/api/samples")
    def samples() -> list[dict[str, Any]]:
        c = con()
        try:
            rows = c.execute("""
                SELECT s.sample_id, s.position, l.verdict IS NOT NULL
                FROM samples s LEFT JOIN labels l USING (sample_id)
                ORDER BY s.position""").fetchall()
        finally:
            c.close()
        return [{"id": r[0], "position": r[1], "done": r[2]} for r in rows]

    @app.get("/api/sample/{sample_id}")
    def sample(sample_id: int) -> dict[str, Any]:
        c = con()
        try:
            s = c.execute("SELECT symbol, scan_date FROM samples WHERE sample_id = ?",
                          [sample_id]).fetchone()  # fmt: skip
            if s is None:
                raise HTTPException(404, "no such sample")
            bars = c.execute(
                """
                SELECT trade_date, open, high, low, close, volume FROM sample_bars
                WHERE sample_id = ? ORDER BY trade_date""",
                [sample_id],
            ).fetchall()
            lab = c.execute(
                """
                SELECT verdict, t1_date, t1_price, lows_json, pivot_date, pivot_price, note
                FROM labels WHERE sample_id = ?""",
                [sample_id],
            ).fetchone()
        finally:
            c.close()
        label = None
        if lab:
            label = {
                "verdict": lab[0],
                "t1": {"date": lab[1].isoformat(), "price": lab[2]} if lab[1] else None,
                "lows": json.loads(lab[3] or "[]"),
                "pivot": {"date": lab[4].isoformat(), "price": lab[5]} if lab[4] else None,
                "note": lab[6],
            }
        return {
            "id": sample_id, "symbol": s[0], "scan_date": s[1].isoformat(),
            "bars": [[b[0].isoformat(), b[1], b[2], b[3], b[4], b[5]] for b in bars],
            "label": label,
        }  # fmt: skip

    @app.post("/api/label/{sample_id}")
    def save(sample_id: int, body: Label) -> dict[str, Any]:
        c = con(read_only=False)
        try:
            known = c.execute("SELECT 1 FROM samples WHERE sample_id = ?", [sample_id]).fetchone()
            if known is None:
                raise HTTPException(404, "no such sample")
            c.execute("DELETE FROM labels WHERE sample_id = ?", [sample_id])
            c.execute("INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?)", [
                sample_id, body.verdict,
                body.t1.date if body.t1 else None, body.t1.price if body.t1 else None,
                json.dumps([{"date": m.date.isoformat(), "price": m.price} for m in body.lows]),
                body.pivot.date if body.pivot else None,
                body.pivot.price if body.pivot else None,
                body.note, datetime.now(UTC)])  # fmt: skip
            done = c.execute("SELECT count(*) FROM labels").fetchone()
        finally:
            c.close()
        return {"saved": sample_id, "labelled": done[0] if done else 0}

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--db", default=str(LABELS))
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        raise SystemExit(f"{db} not found: run research/vcp2/label_sample.py first")
    uvicorn.run(make_app(db), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
