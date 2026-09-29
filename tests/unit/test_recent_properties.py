"""Property test for the Últimas_Facturas pagination (task 8.7).

Requirements: 16.8, 16.10, 16.14, 16.16.
"""
from __future__ import annotations

import tempfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models import EntryInput
from app.repository import EntryRepository, connect, init_schema

PAGE_SIZE = 10
MAX_ENTRIES = 35

repo = EntryRepository()

_BASE = datetime(2025, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
# A small pool of instants makes ties on created_at frequent; the offsets also
# include microsecond differences so ordering at that precision is exercised.
_INSTANTS = [
    _BASE + timedelta(microseconds=us)
    for us in (0, 1, 999_999, 1_000_000, 60_000_000, 86_400_000_000)
]

_created_ats = st.one_of(
    st.sampled_from(_INSTANTS),
    st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2030, 12, 31),
        timezones=st.just(timezone.utc),
    ),
)

_entries = st.lists(
    st.tuples(st.sampled_from(["gasto", "ingreso"]), _created_ats),
    max_size=MAX_ENTRIES,
)


def _input(entry_type: str) -> EntryInput:
    return EntryInput(
        invoice_date=date(2025, 3, 1),
        entry_type=entry_type,
        total=Decimal("12.10"),
    )


# Feature: invoice-reader, Property 23: Paginación de Últimas_Facturas sin duplicados ni omisiones
@settings(max_examples=100, deadline=None)
@given(entries=_entries)
def test_recent_pagination_has_no_duplicates_or_omissions(entries):
    """**Validates: Requirements 16.8, 16.10, 16.14, 16.16**"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "invoices.db"
        init_schema(path)
        with closing(connect(path)) as conn:
            inserted: list[tuple[datetime, int, str]] = []
            for index, (entry_type, created_at) in enumerate(entries):
                entry_id = repo.insert(
                    conn, _input(entry_type), "", f"img-{index}.jpg", created_at
                )
                inserted.append((created_at, entry_id, entry_type))

            n = len(inserted)
            expected = [
                (entry_id, entry_type)
                for _, entry_id, entry_type in sorted(inserted, reverse=True)
            ]
            last_page = -(-n // PAGE_SIZE)  # ceil(N / 10)

            collected: list[tuple[int, str]] = []
            for p in range(1, last_page + 1):
                items, has_more = repo.recent(conn, offset=(p - 1) * PAGE_SIZE, limit=PAGE_SIZE)
                assert len(items) <= PAGE_SIZE
                if p < last_page:
                    assert len(items) == PAGE_SIZE
                else:
                    assert len(items) >= 1
                assert has_more is (p * PAGE_SIZE < n)
                collected.extend((item.id, item.entry_type) for item in items)

            assert collected == expected
            assert len({entry_id for entry_id, _ in collected}) == n

            for p in (last_page + 1, last_page + 2):
                assert repo.recent(conn, offset=(p - 1) * PAGE_SIZE, limit=PAGE_SIZE) == ([], False)
