"""Unit tests for EntryRepository and the cents/timestamp helpers (tasks 8.2 / 8.5).

Requirements: 6.1, 6.4, 8.1, 8.2, 8.3, 8.4, 9.1, 9.3, 14.6.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import Entry, EntryFilter, EntryInput, Totals
from app.repository import (
    DEFAULT_PAGE_SIZE,
    EntryRepository,
    cents_to_decimal,
    connect,
    decimal_to_cents,
    format_timestamp,
    init_schema,
)

NOW = datetime(2025, 3, 1, 10, 15, 0, 123456, tzinfo=timezone.utc)
LATER = datetime(2025, 3, 2, 8, 0, 0, tzinfo=timezone.utc)

repo = EntryRepository()


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "invoices.db"
    init_schema(path)
    # closing() so Windows can remove tmp_path (WAL/SHM files) afterwards.
    with closing(connect(path)) as c:
        yield c


def _input(**overrides) -> EntryInput:
    values = {
        "invoice_date": date(2025, 3, 1),
        "entry_type": "gasto",
        "total": Decimal("12.10"),
        "base_amount": Decimal("10.00"),
        "vat_amount": Decimal("2.10"),
        "supplier": "Proveedor S.L.",
        "tax_id": "B12345678",
        "invoice_number": "F-001",
        "concept": "Luz",
        "category": "Suministros",
    }
    values.update(overrides)
    return EntryInput(**values)


_counter = iter(range(10**9))


def _add(conn, now: datetime = NOW, **overrides) -> int:
    name = f"{next(_counter):032x}.jpg"
    return repo.insert(conn, _input(**overrides), "texto", name, now)


def _fields(entry: Entry) -> EntryInput:
    return EntryInput(
        invoice_date=entry.invoice_date,
        entry_type=entry.entry_type,
        total=entry.total,
        base_amount=entry.base_amount,
        vat_amount=entry.vat_amount,
        supplier=entry.supplier,
        tax_id=entry.tax_id,
        invoice_number=entry.invoice_number,
        concept=entry.concept,
        category=entry.category,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("amount", "cents"),
    [
        (Decimal("0"), 0),
        (Decimal("0.01"), 1),
        (Decimal("12.1"), 1210),
        (Decimal("12.100"), 1210),
        (Decimal("999999999.99"), 99999999999),
    ],
)
def test_decimal_to_cents(amount, cents):
    assert decimal_to_cents(amount) == cents


def test_decimal_to_cents_rejects_more_than_two_decimals():
    with pytest.raises(ValueError):
        decimal_to_cents(Decimal("1.005"))


@pytest.mark.parametrize(("cents", "text"), [(0, "0.00"), (5, "0.05"), (1210, "12.10")])
def test_cents_to_decimal_has_two_decimals(cents, text):
    value = cents_to_decimal(cents)
    assert str(value) == text
    assert value.as_tuple().exponent == -2


def test_format_timestamp_is_utc_with_microseconds():
    madrid = timezone(timedelta(hours=1))
    assert format_timestamp(datetime(2025, 3, 1, 11, 15, tzinfo=madrid)) == (
        "2025-03-01T10:15:00.000000+00:00"
    )


# --------------------------------------------------------------------------- #
# insert / get / update / delete
# --------------------------------------------------------------------------- #


def test_insert_get_round_trip(conn):
    entry_input = _input()
    entry_id = repo.insert(conn, entry_input, "TEXTO OCR\nlínea", "a" * 32 + ".png", NOW)

    entry = repo.get(conn, entry_id)

    assert entry is not None
    assert entry.id == entry_id
    assert _fields(entry) == entry_input
    assert entry.total.as_tuple().exponent == -2
    assert entry.ocr_text == "TEXTO OCR\nlínea"
    assert entry.image_filename == "a" * 32 + ".png"
    assert entry.created_at == NOW
    assert entry.updated_at is None


def test_insert_stores_amounts_as_integer_cents_and_fixed_created_at(conn):
    entry_id = _add(conn, now=datetime(2025, 3, 1, 12, 0, tzinfo=timezone(timedelta(hours=2))))
    row = conn.execute(
        "SELECT base_cents, vat_cents, total_cents, created_at FROM entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    assert row == (1000, 210, 1210, "2025-03-01T10:00:00.000000+00:00")


def test_optional_fields_round_trip_as_none(conn):
    entry_input = EntryInput(invoice_date=date(2024, 1, 31), entry_type="ingreso", total=Decimal("0.00"))
    entry_id = repo.insert(conn, entry_input, "", "b" * 32 + ".jpg", NOW)
    assert _fields(repo.get(conn, entry_id)) == entry_input


def test_sql_looking_strings_are_stored_as_data(conn):
    nasty = "x'); DROP TABLE entries; -- \"ñ€\u202e"
    entry_input = _input(supplier=nasty, concept=nasty, category=nasty, invoice_number=nasty, tax_id=nasty)
    entry_id = repo.insert(conn, entry_input, nasty, "c" * 32 + ".jpg", NOW)

    assert _fields(repo.get(conn, entry_id)) == entry_input
    assert repo.get(conn, entry_id).ocr_text == nasty
    page = repo.list(conn, EntryFilter(category=nasty), 1)
    assert [e.id for e in page.items] == [entry_id]
    assert repo.list(conn, EntryFilter(category="' OR '1'='1"), 1).total == 0


def test_duplicate_image_filename_is_rejected(conn):
    repo.insert(conn, _input(), "", "d" * 32 + ".jpg", NOW)
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert(conn, _input(), "", "d" * 32 + ".jpg", NOW)


def test_insert_does_not_commit(conn):
    conn.execute("BEGIN IMMEDIATE")
    _add(conn)
    conn.rollback()
    assert repo.list(conn, EntryFilter(), 1).total == 0


def test_update_replaces_fields_and_sets_updated_at(conn):
    entry_id = repo.insert(conn, _input(), "ocr", "e" * 32 + ".jpg", NOW)
    changed = _input(
        invoice_date=date(2024, 12, 31),
        entry_type="ingreso",
        total=Decimal("100.5"),
        base_amount=None,
        vat_amount=None,
        supplier=None,
        category="Otros",
    )

    assert repo.update(conn, entry_id, changed, LATER) is True

    entry = repo.get(conn, entry_id)
    assert _fields(entry) == changed
    assert entry.updated_at == LATER
    assert (entry.created_at, entry.ocr_text, entry.image_filename) == (NOW, "ocr", "e" * 32 + ".jpg")


def test_delete_returns_entry_and_removes_row(conn):
    entry_id = _add(conn)
    other_id = _add(conn)

    deleted = repo.delete(conn, entry_id)

    assert deleted is not None and deleted.id == entry_id
    assert repo.get(conn, entry_id) is None
    assert repo.get(conn, other_id) is not None


def test_missing_ids(conn):
    _add(conn)
    assert repo.get(conn, 999) is None
    assert repo.update(conn, 999, _input(), LATER) is False
    assert repo.delete(conn, 999) is None
    assert repo.list(conn, EntryFilter(), 1).total == 1


# --------------------------------------------------------------------------- #
# list / totals
# --------------------------------------------------------------------------- #


def test_list_orders_by_invoice_date_then_id_desc(conn):
    a = _add(conn, invoice_date=date(2025, 1, 10))
    b = _add(conn, invoice_date=date(2025, 5, 1))
    c = _add(conn, invoice_date=date(2025, 1, 10))
    d = _add(conn, invoice_date=date(2024, 12, 31))

    page = repo.list(conn, EntryFilter(), 1)

    assert [e.id for e in page.items] == [b, c, a, d]
    assert (page.page, page.page_size, page.total) == (1, DEFAULT_PAGE_SIZE, 4)


def test_list_paginates_by_20(conn):
    ids = [_add(conn, invoice_date=date(2025, 1, 1) + timedelta(days=i)) for i in range(45)]
    expected = list(reversed(ids))

    pages = [repo.list(conn, EntryFilter(), p) for p in (1, 2, 3)]

    assert [len(p.items) for p in pages] == [20, 20, 5]
    assert [e.id for p in pages for e in p.items] == expected
    assert all(p.total == 45 and p.total_pages == 3 for p in pages)


def test_list_custom_page_size(conn):
    ids = [_add(conn) for _ in range(5)]
    page = repo.list(conn, EntryFilter(), 2, page_size=2)
    assert [e.id for e in page.items] == [ids[2], ids[1]]


def test_page_out_of_range_returns_no_items_with_total(conn):
    _add(conn)
    page = repo.list(conn, EntryFilter(), 5)
    assert (page.items, page.page, page.total) == ([], 5, 1)


def test_empty_database(conn):
    page = repo.list(conn, EntryFilter(), 1)
    assert (page.items, page.total, page.total_pages) == ([], 0, 1)
    assert repo.totals(conn, EntryFilter()) == Totals(Decimal("0.00"), Decimal("0.00"))


@pytest.mark.parametrize(("page", "page_size"), [(0, 20), (-1, 20), (1, 0)])
def test_list_rejects_invalid_page_arguments(conn, page, page_size):
    with pytest.raises(ValueError):
        repo.list(conn, EntryFilter(), page, page_size)


@pytest.fixture
def mixed(conn):
    """Entries of both types, several categories and dates; returns name -> id."""
    return {
        "g_jan_hogar": _add(conn, invoice_date=date(2025, 1, 15), category="Hogar", total=Decimal("10.00")),
        "g_feb_hogar": _add(conn, invoice_date=date(2025, 2, 1), category="Hogar", total=Decimal("20.50")),
        "g_feb_ocio": _add(conn, invoice_date=date(2025, 2, 28), category="Ocio", total=Decimal("5.25")),
        "i_feb_hogar": _add(
            conn, invoice_date=date(2025, 2, 10), category="Hogar", entry_type="ingreso", total=Decimal("100.00")
        ),
        "i_mar_none": _add(
            conn, invoice_date=date(2025, 3, 1), category=None, entry_type="ingreso", total=Decimal("7.77")
        ),
    }


def _ids(conn, flt):
    return {e.id for e in repo.list(conn, flt, 1).items}


def test_filter_by_type(conn, mixed):
    assert _ids(conn, EntryFilter(entry_type="ingreso")) == {mixed["i_feb_hogar"], mixed["i_mar_none"]}


def test_filter_by_category(conn, mixed):
    assert _ids(conn, EntryFilter(category="Ocio")) == {mixed["g_feb_ocio"]}


def test_filter_by_inclusive_date_range(conn, mixed):
    flt = EntryFilter(date_from=date(2025, 2, 1), date_to=date(2025, 2, 28))
    assert _ids(conn, flt) == {mixed["g_feb_hogar"], mixed["g_feb_ocio"], mixed["i_feb_hogar"]}


def test_filters_are_combined(conn, mixed):
    flt = EntryFilter(entry_type="gasto", category="Hogar", date_from=date(2025, 2, 1), date_to=date(2025, 12, 31))
    page = repo.list(conn, flt, 1)
    assert [e.id for e in page.items] == [mixed["g_feb_hogar"]]
    assert page.total == 1


def test_totals_without_filters(conn, mixed):
    assert repo.totals(conn, EntryFilter()) == Totals(expenses=Decimal("35.75"), income=Decimal("107.77"))


def test_totals_with_filters(conn, mixed):
    totals = repo.totals(conn, EntryFilter(category="Hogar", date_from=date(2025, 2, 1)))
    assert totals == Totals(expenses=Decimal("20.50"), income=Decimal("100.00"))
    assert totals.expenses.as_tuple().exponent == -2


def test_totals_by_type_only_counts_that_type(conn, mixed):
    assert repo.totals(conn, EntryFilter(entry_type="gasto")) == Totals(Decimal("35.75"), Decimal("0.00"))
