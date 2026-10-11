"""Sector classification (DATA_SPECIFICATION §48.1, step S1)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from vcp_scanner.data.providers.nse_index_lists import parse_index_list
from vcp_scanner.data.repositories.duckdb_classification_repository import (
    SOURCE_QUOTE,
    Classification,
    DuckDBClassificationRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore

SAMPLE = (
    "﻿Company Name,Industry,Symbol,Series,ISIN Code\n"
    "360 ONE WAM Ltd.,Financial Services,360ONE,EQ,INE466L01038\n"
    "ABB India Ltd.,Capital Goods,ABB,EQ,INE117A01022\n"
    "Broken Ltd.,,BROKEN,EQ,INE000000000\n"
)


def test_parse_index_list() -> None:
    rows = parse_index_list(SAMPLE, "ind_niftytotalmarket_list.csv")
    assert [(r.symbol, r.sector, r.isin) for r in rows] == [
        ("360ONE", "Financial Services", "INE466L01038"),
        ("ABB", "Capital Goods", "INE117A01022"),
    ]


@pytest.fixture
def repo() -> DuckDBClassificationRepository:
    store = DuckDBStore(":memory:")
    store.migrate()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for iid, sym, isin in (("I1", "ABB", "INE117A01022"), ("I2", "NEWNAME", "INE466L01038")):
        store.conn.execute(
            "INSERT INTO instruments (instrument_id, isin, symbol, is_active, created_at,"
            " updated_at) VALUES (?, ?, ?, true, ?, ?)",
            [iid, isin, sym, now, now],
        )
    return DuckDBClassificationRepository(store)


def test_match_by_isin_first_then_symbol(repo: DuckDBClassificationRepository) -> None:
    # 360ONE's ISIN belongs to an instrument now called NEWNAME: the ISIN wins.
    ids = repo.match_instruments([("360ONE", "INE466L01038"), ("ABB", None), ("NONE", None)])
    assert ids == {"360ONE": "I2", "ABB": "I1"}


def test_changes_close_the_old_row_and_nothing_is_overwritten(
    repo: DuckDBClassificationRepository,
) -> None:
    r1 = repo.apply([Classification("I1", "Capital Goods")], date(2026, 10, 11))
    assert (r1.inserted, r1.changed) == (1, 0)
    r2 = repo.apply([Classification("I1", "Capital Goods")], date(2026, 10, 16))
    assert r2.unchanged == 1
    r3 = repo.apply([Classification("I1", "Power")], date(2026, 10, 23))
    assert r3.changed == 1
    rows = repo._conn.execute(
        "SELECT sector, valid_from, valid_to FROM instrument_classification ORDER BY valid_from"
    ).fetchall()
    assert rows == [("Capital Goods", date(2026, 10, 11), date(2026, 10, 23)),
                    ("Power", date(2026, 10, 23), None)]  # fmt: skip


def test_index_list_does_not_replace_the_finer_quote_classification(
    repo: DuckDBClassificationRepository,
) -> None:
    fine = Classification("I1", "Capital Goods", "Industrials", "Electrical Equipment",
                          "Heavy Electrical Equipment", SOURCE_QUOTE)  # fmt: skip
    repo.apply([fine], date(2026, 10, 11))
    assert repo.apply([Classification("I1", "Capital Goods")], date(2026, 10, 16)).unchanged == 1
    assert repo.current("I1") == fine
    assert repo.coverage(["I1", "I2"]) == 1
