"""NSE index closes (Market Overview step 3, FRONTEND_SPECIFICATION §67.28): the daily file,
its cache and `vcp indices update`. Display only."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner.cli_indices import update_indices
from vcp_scanner.data.providers.nse_index_closes import (
    URL,
    IndexFile,
    NseIndexCloseProvider,
    parse_index_closes,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.errors import ProviderError

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "indices" / "ind_close_all_09102026.csv"
DAY = date(2026, 10, 9)


def test_parse_the_real_file_known_answers() -> None:
    rows = {r.index_name: r for r in parse_index_closes(FIX.read_bytes(), DAY)}
    assert len(rows) == 167
    n50 = rows["Nifty 50"]
    assert (n50.open, n50.high, n50.low, n50.close) == (22314.95, 22580.75, 22294.75, 22520.45)
    assert n50.volume == 296205852 and n50.pe == 19.27
    assert rows["Nifty 500"].close == 21866.65
    assert rows["Nifty IT"].close == 28574.9


def test_a_file_of_another_day_is_refused() -> None:
    with pytest.raises(ProviderError, match="no index rows"):
        parse_index_closes(FIX.read_bytes(), date(2026, 10, 8))


class Resp:
    def __init__(self, status: int, content: bytes = b"") -> None:
        self.status_code, self.content = status, content


class Session:
    def __init__(self, answers: dict[str, Resp]) -> None:
        self.answers, self.calls = answers, []  # type: ignore[var-annotated]
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: float = 0) -> Resp:
        self.calls.append(url)
        return self.answers.get(url, Resp(404))


def test_fetch_caches_the_file_and_404_is_no_file(tmp_path: Path) -> None:
    session = Session({URL.format(DAY): Resp(200, FIX.read_bytes())})
    p = NseIndexCloseProvider(tmp_path, session=session, sleep=lambda _s: None)
    got = p.fetch_day(DAY)
    assert got.status == "OK" and not got.from_cache and p.cache_path(DAY).exists()
    again = p.fetch_day(DAY)
    assert again.from_cache and len(session.calls) == 1  # the cache answers, no request
    assert p.fetch_day(date(2026, 10, 2)).status == "NOT_FOUND"  # Gandhi Jayanti: no session


def test_a_server_error_raises_and_nothing_is_cached(tmp_path: Path) -> None:
    session = Session({URL.format(DAY): Resp(503)})
    p = NseIndexCloseProvider(tmp_path, session=session, sleep=lambda _s: None)
    with pytest.raises(ProviderError, match="HTTP 503"):
        p.fetch_day(DAY)
    assert not p.cache_path(DAY).exists()


class FakeProvider:
    def __init__(self, files: dict[date, IndexFile]) -> None:
        self.files, self.asked = files, []  # type: ignore[var-annotated]

    def fetch_day(self, day: date) -> IndexFile:
        self.asked.append(day)
        if day not in self.files:
            raise ProviderError(f"NSE index closes {day}: HTTP 503")
        return self.files[day]


def _db(tmp_path: Path, days: list[date]) -> Path:
    db = tmp_path / "x.duckdb"
    with DuckDBStore(str(db)) as store:
        store.migrate()
        for d in days:
            store.conn.execute(
                "INSERT INTO bhavcopy_files (trade_date, sha256, status, url, file_format,"
                " recorded_at) VALUES (?, ?, 'OK', 'u', 'CM_UDIFF', ?)",
                [d, d.isoformat(), datetime.now(UTC)],
            )
    return db


def test_update_stores_each_trading_day_once(tmp_path: Path, capsys: Any) -> None:
    other = date(2026, 10, 8)
    db = _db(tmp_path, [other, DAY])
    payload = FIX.read_bytes()
    provider = FakeProvider({DAY: IndexFile(DAY, "OK", payload, "h")})
    # 8 Oct fails (HTTP 503): a warning, and it is asked again next time
    assert update_indices(db, date(2026, 10, 1), provider=provider) == 1
    assert "WARNING" in capsys.readouterr().out
    with DuckDBStore(str(db)) as store:
        c = store.conn
        assert (
            c.execute("SELECT count(*) FROM index_daily WHERE trade_date = ?", [DAY]).fetchone()[0]
            == 167
        )
        assert (
            c.execute("SELECT close FROM index_daily WHERE index_name = 'Nifty 50'").fetchone()[0]
            == 22520.45
        )
    provider.files[other] = IndexFile(other, "NOT_FOUND")
    assert update_indices(db, date(2026, 10, 1), provider=provider) == 1
    assert provider.asked == [other, DAY, other]  # 9 Oct is not asked again
    with DuckDBStore(str(db)) as store:
        rows = store.conn.execute(
            "SELECT trade_date, status, rows FROM index_files ORDER BY 1"
        ).fetchall()
    assert rows == [(other, "NOT_FOUND", 0), (DAY, "OK", 167)]
