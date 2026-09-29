"""Pruebas de propiedades de ``EntryRepository.list`` y ``EntryRepository.totals``.

Cada ejemplo usa su propia base de datos en un ``TemporaryDirectory`` y cierra
la conexión antes de borrarlo, para que Windows pueda eliminar los ficheros WAL.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlite3 import Connection

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models import EntryFilter, EntryInput
from app.repository import EntryRepository, connect, init_schema
from tests.strategies import amounts, dates

PAGE_SIZE = 20
MAX_ENTRIES = 30

# Rango de fechas estrecho para que haya empates en ``invoice_date`` y se
# ejercite el desempate por ``id``.
_DATE_MIN = date(2024, 1, 1)
_DATE_MAX = date(2024, 1, 15)
_CATEGORIES = ("Hogar", "Ocio", "Salud")
_NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)

_entries = st.builds(
    EntryInput,
    invoice_date=dates(_DATE_MIN, _DATE_MAX),
    entry_type=st.sampled_from(("gasto", "ingreso")),
    total=amounts(max_cents=10**8),
    category=st.one_of(st.none(), st.sampled_from(_CATEGORIES)),
)

_filters = st.builds(
    EntryFilter,
    entry_type=st.one_of(st.none(), st.sampled_from(("gasto", "ingreso"))),
    category=st.one_of(st.none(), st.sampled_from(_CATEGORIES)),
    date_from=st.one_of(st.none(), dates(_DATE_MIN, _DATE_MAX)),
    date_to=st.one_of(st.none(), dates(_DATE_MIN, _DATE_MAX)),
)


@contextmanager
def _populated_db(entries: list[EntryInput]) -> Iterator[tuple[Connection, list[int]]]:
    """Base de datos temporal con ``entries`` insertados; devuelve la conexión y los ids."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        init_schema(db_path)
        with closing(connect(db_path)) as conn:
            repo = EntryRepository()
            conn.execute("BEGIN IMMEDIATE")
            ids = [
                repo.insert(conn, entry, "", f"img-{index}.jpg", _NOW)
                for index, entry in enumerate(entries)
            ]
            conn.commit()
            yield conn, ids


def _all_pages(conn: Connection, flt: EntryFilter) -> list:
    """Recorre todas las páginas del listado y devuelve las páginas obtenidas."""
    repo = EntryRepository()
    first = repo.list(conn, flt, page=1)
    pages = [first]
    for number in range(2, first.total_pages + 1):
        pages.append(repo.list(conn, flt, page=number))
    return pages


def _matches(entry: EntryInput, flt: EntryFilter) -> bool:
    """Modelo de referencia en memoria: conjunción de condiciones, rango inclusivo."""
    return (
        (flt.entry_type is None or entry.entry_type == flt.entry_type)
        and (flt.category is None or entry.category == flt.category)
        and (flt.date_from is None or entry.invoice_date >= flt.date_from)
        and (flt.date_to is None or entry.invoice_date <= flt.date_to)
    )


# Feature: invoice-reader, Property 18: Listado ordenado y paginado
@settings(max_examples=100, deadline=None)
@given(entries=st.lists(_entries, max_size=MAX_ENTRIES))
def test_listing_is_sorted_and_paginated(entries: list[EntryInput]) -> None:
    """**Validates: Requirements 8.1, 8.2**"""
    with _populated_db(entries) as (conn, ids):
        pages = _all_pages(conn, EntryFilter())

        assert all(page.total == len(entries) for page in pages)
        assert all(page.page_size == PAGE_SIZE for page in pages)
        assert [page.page for page in pages] == list(range(1, len(pages) + 1))
        for page in pages[:-1]:
            assert len(page.items) == PAGE_SIZE
        assert len(pages[-1].items) <= PAGE_SIZE

        listed = [item for page in pages for item in page.items]
        listed_ids = [item.id for item in listed]
        assert sorted(listed_ids) == sorted(ids)  # cada Apunte exactamente una vez

        keys = [(item.invoice_date, item.id) for item in listed]
        assert keys == sorted(keys, reverse=True)

        # Los datos listados corresponden a lo insertado.
        by_id = dict(zip(ids, entries))
        for item in listed:
            original = by_id[item.id]
            assert (item.invoice_date, item.entry_type, item.total, item.category) == (
                original.invoice_date, original.entry_type, original.total, original.category,
            )


# Feature: invoice-reader, Property 19: Filtros y totales frente a un modelo de referencia
@settings(max_examples=100, deadline=None)
@given(entries=st.lists(_entries, max_size=MAX_ENTRIES), flt=_filters)
def test_filters_and_totals_match_reference_model(
    entries: list[EntryInput], flt: EntryFilter
) -> None:
    """**Validates: Requirements 8.3, 8.4**"""
    with _populated_db(entries) as (conn, ids):
        expected = {entry_id: entry for entry_id, entry in zip(ids, entries) if _matches(entry, flt)}

        pages = _all_pages(conn, flt)
        listed_ids = [item.id for page in pages for item in page.items]
        assert sorted(listed_ids) == sorted(expected)
        assert all(page.total == len(expected) for page in pages)

        totals = EntryRepository().totals(conn, flt)
        expected_expenses = sum(
            (e.total for e in expected.values() if e.entry_type == "gasto"), Decimal("0.00")
        )
        expected_income = sum(
            (e.total for e in expected.values() if e.entry_type == "ingreso"), Decimal("0.00")
        )
        assert totals.expenses == expected_expenses
        assert totals.income == expected_income
