"""Unit tests for the Portada queries of EntryRepository (tasks 8.6 / 8.8).

Requirements: 16.3, 16.4, 16.5, 16.7, 16.8, 16.10, 16.14, 16.16, 16.17, 14.6.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import EntryInput
from app.repository import EntryRepository, connect, init_schema

NOW = datetime(2025, 3, 1, 10, 15, 0, 123456, tzinfo=timezone.utc)

repo = EntryRepository()
_counter = iter(range(10**9))


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "invoices.db"
    init_schema(path)
    # closing() so Windows can remove tmp_path (WAL/SHM files) afterwards.
    with closing(connect(path)) as c:
        yield c


def _add(
    conn,
    invoice_date: date = date(2025, 3, 1),
    total: str = "10.00",
    entry_type: str = "gasto",
    now: datetime = NOW,
) -> int:
    entry = EntryInput(invoice_date=invoice_date, entry_type=entry_type, total=Decimal(total))
    return repo.insert(conn, entry, "texto", f"{next(_counter):032x}.jpg", now)


# --------------------------------------------------------------------------- #
# monthly_expense_totals
# --------------------------------------------------------------------------- #


def test_monthly_totals_empty_db(conn):
    assert repo.monthly_expense_totals(conn, 2024, 2025) == {}


def test_monthly_totals_groups_by_year_and_month(conn):
    _add(conn, date(2025, 3, 1), "10.00")
    _add(conn, date(2025, 3, 31), "2.55")
    _add(conn, date(2025, 4, 15), "1.00")
    _add(conn, date(2024, 3, 10), "7.00")

    assert repo.monthly_expense_totals(conn, 2024, 2025) == {
        (2025, 3): 1255,
        (2025, 4): 100,
        (2024, 3): 700,
    }


def test_monthly_totals_year_boundaries(conn):
    _add(conn, date(2024, 12, 31), "5.00")
    _add(conn, date(2025, 1, 1), "3.00")

    totals = repo.monthly_expense_totals(conn, 2024, 2025)

    assert totals == {(2024, 12): 500, (2025, 1): 300}


def test_monthly_totals_exclude_income(conn):
    _add(conn, date(2025, 5, 5), "10.00", entry_type="gasto")
    _add(conn, date(2025, 5, 6), "99.00", entry_type="ingreso")
    _add(conn, date(2025, 6, 1), "50.00", entry_type="ingreso")

    assert repo.monthly_expense_totals(conn, 2024, 2025) == {(2025, 5): 1000}


def test_monthly_totals_exclude_years_outside_range(conn):
    _add(conn, date(2023, 12, 31), "1.00")
    _add(conn, date(2026, 1, 1), "2.00")
    _add(conn, date(2024, 1, 1), "3.00")
    _add(conn, date(2025, 12, 31), "4.00")

    assert repo.monthly_expense_totals(conn, 2024, 2025) == {(2024, 1): 300, (2025, 12): 400}


def test_monthly_totals_single_year(conn):
    _add(conn, date(2024, 6, 1), "1.00")
    _add(conn, date(2025, 6, 1), "2.00")

    assert repo.monthly_expense_totals(conn, 2025, 2025) == {(2025, 6): 200}


@pytest.mark.parametrize(("first", "last"), [(2025, 2024), (0, 2025), (2024, 9999)])
def test_monthly_totals_invalid_range(conn, first, last):
    with pytest.raises(ValueError):
        repo.monthly_expense_totals(conn, first, last)


# --------------------------------------------------------------------------- #
# recent
# --------------------------------------------------------------------------- #


def test_recent_empty_db(conn):
    assert repo.recent(conn, 0) == ([], False)


def test_recent_orders_by_created_at_desc_and_includes_both_types(conn):
    older = _add(conn, entry_type="ingreso", now=NOW)
    newer = _add(conn, entry_type="gasto", now=NOW + timedelta(seconds=1))
    # Invoice date does not affect the order, only created_at.
    oldest = _add(conn, date(2030, 1, 1), now=NOW - timedelta(days=1))

    items, has_more = repo.recent(conn, 0)

    assert [e.id for e in items] == [newer, older, oldest]
    assert {e.entry_type for e in items} == {"gasto", "ingreso"}
    assert has_more is False


def test_recent_created_at_tie_resolved_by_id_desc(conn):
    ids = [_add(conn, now=NOW) for _ in range(3)]

    items, _ = repo.recent(conn, 0)

    assert [e.id for e in items] == sorted(ids, reverse=True)


def test_recent_exactly_ten_entries_has_no_more(conn):
    for i in range(10):
        _add(conn, now=NOW + timedelta(seconds=i))

    items, has_more = repo.recent(conn, 0)

    assert len(items) == 10
    assert has_more is False


def test_recent_eleven_entries_has_more(conn):
    ids = [_add(conn, now=NOW + timedelta(seconds=i)) for i in range(11)]

    first, has_more = repo.recent(conn, 0)
    second, has_more_2 = repo.recent(conn, 10)

    assert [e.id for e in first] == ids[:0:-1]
    assert has_more is True
    assert [e.id for e in second] == [ids[0]]
    assert has_more_2 is False


def test_recent_offset_beyond_end(conn):
    for _ in range(3):
        _add(conn)

    assert repo.recent(conn, 10) == ([], False)


def test_recent_custom_limit(conn):
    ids = [_add(conn, now=NOW + timedelta(seconds=i)) for i in range(3)]

    items, has_more = repo.recent(conn, 1, limit=1)

    assert [e.id for e in items] == [ids[1]]
    assert has_more is True


@pytest.mark.parametrize(("offset", "limit"), [(-1, 10), (0, 0), (0, -5)])
def test_recent_invalid_args(conn, offset, limit):
    with pytest.raises(ValueError):
        repo.recent(conn, offset, limit)


# --------------------------------------------------------------------------- #
# Indexes
# --------------------------------------------------------------------------- #


def test_portada_indexes_present(conn):
    names = {row[1] for row in conn.execute("PRAGMA index_list(entries)")}

    assert {"ix_entries_created", "ix_entries_type_date"} <= names


def test_portada_index_columns(conn):
    def columns(index: str) -> list[tuple[str, int]]:
        # index_xinfo: (seqno, cid, name, desc, coll, key); key=1 for indexed columns.
        return [
            (row[2], row[3])
            for row in conn.execute(f"PRAGMA index_xinfo({index})")
            if row[5] == 1
        ]

    assert columns("ix_entries_created") == [("created_at", 1), ("id", 1)]
    assert columns("ix_entries_type_date") == [("entry_type", 0), ("invoice_date", 0)]
