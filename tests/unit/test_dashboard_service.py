"""Unit tests for DashboardService (Req. 16.2-16.8, 16.14, 16.16, 16.17)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.formatting import MONTH_NAMES_ES
from app.models import EntryInput, ExpenseSummary
from app.repository import EntryRepository, connect, init_schema
from app.services import RECENT_PAGE_SIZE, DashboardService

ZERO = Decimal("0.00")
BASE_CREATED = datetime(2025, 1, 1, 8, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class TrackingConnection:
    """Wraps a real connection and records whether ``close`` was called."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.closed = False

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        self.closed = True
        self._conn.close()


class CountingRepository(EntryRepository):
    def __init__(self) -> None:
        self.monthly_calls: list[tuple[int, int]] = []

    def monthly_expense_totals(self, conn, first_year, last_year):
        self.monthly_calls.append((first_year, last_year))
        return super().monthly_expense_totals(conn, first_year, last_year)


class FailingRepository(EntryRepository):
    def monthly_expense_totals(self, conn, first_year, last_year):
        raise sqlite3.OperationalError("boom")

    def recent(self, conn, offset, limit=10):
        raise sqlite3.OperationalError("boom")


@pytest.fixture
def db(settings: Settings) -> Path:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    init_schema(settings.db_path)
    return settings.db_path


@pytest.fixture
def opened(db: Path) -> list[TrackingConnection]:
    return []


@pytest.fixture
def connect_db(db: Path, opened: list[TrackingConnection]):
    def _connect() -> TrackingConnection:
        conn = TrackingConnection(connect(db))
        opened.append(conn)
        return conn

    return _connect


def _service(connect_db, now: datetime, entries: EntryRepository | None = None) -> DashboardService:
    return DashboardService(entries or EntryRepository(), connect_db, Clock(now))


def _add(
    db: Path,
    invoice_date: date,
    total: str,
    entry_type: str = "gasto",
    created_at: datetime = BASE_CREATED,
    image: str | None = None,
) -> int:
    conn = connect(db)
    try:
        entry = EntryInput(invoice_date=invoice_date, entry_type=entry_type, total=Decimal(total))
        name = image or f"{uuid4().hex}.jpg"
        return EntryRepository().insert(conn, entry, "", name, created_at)
    finally:
        conn.close()


def _assert_all_closed(opened: list[TrackingConnection]) -> None:
    assert opened, "no connection was opened"
    assert all(conn.closed for conn in opened)


# -- expense_summary ------------------------------------------------------------------ #


def test_empty_database_gives_zeros_and_twelve_spanish_months(connect_db, opened) -> None:
    summary = _service(connect_db, datetime(2025, 6, 15, tzinfo=timezone.utc)).expense_summary()

    assert isinstance(summary, ExpenseSummary)
    assert summary.current.year == 2025
    assert summary.previous.year == 2024
    assert summary.current.total == ZERO and str(summary.current.total) == "0.00"
    assert summary.previous.total == ZERO and str(summary.previous.total) == "0.00"
    assert [m.month for m in summary.months] == list(range(1, 13))
    assert [m.name for m in summary.months] == list(MONTH_NAMES_ES)
    assert summary.months[0].name == "Enero" and summary.months[-1].name == "Diciembre"
    assert all(str(m.previous) == "0.00" and str(m.current) == "0.00" for m in summary.months)
    _assert_all_closed(opened)


def test_fixed_clock_sums_expenses_per_month_and_year(db, connect_db, opened) -> None:
    _add(db, date(2025, 1, 1), "10.50")
    _add(db, date(2025, 1, 31), "0.25")
    _add(db, date(2025, 12, 31), "100.00")
    _add(db, date(2024, 3, 15), "7.00")
    _add(db, date(2024, 12, 31), "1.99")
    _add(db, date(2025, 3, 10), "999.00", entry_type="ingreso")  # excluded (16.5)
    _add(db, date(2023, 12, 31), "50.00")  # year - 2, excluded
    _add(db, date(2026, 1, 1), "80.00")  # next year, excluded

    summary = _service(connect_db, datetime(2025, 7, 1, 10, 0, tzinfo=timezone.utc)).expense_summary()

    months = {m.month: m for m in summary.months}
    assert months[1].current == Decimal("10.75")
    assert months[12].current == Decimal("100.00")
    assert months[3].current == ZERO
    assert months[3].previous == Decimal("7.00")
    assert months[12].previous == Decimal("1.99")
    assert months[1].previous == ZERO
    assert summary.current.total == Decimal("110.75")
    assert summary.previous.total == Decimal("8.99")
    assert summary.current.total == sum(m.current for m in summary.months)
    assert summary.previous.total == sum(m.previous for m in summary.months)
    _assert_all_closed(opened)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc),
    ],
)
def test_year_boundaries_of_the_clock(db, connect_db, now: datetime) -> None:
    _add(db, date(2025, 6, 1), "3.00")
    _add(db, date(2024, 6, 1), "2.00")

    summary = _service(connect_db, now).expense_summary()

    assert (summary.previous.year, summary.current.year) == (2024, 2025)
    assert summary.current.total == Decimal("3.00")
    assert summary.previous.total == Decimal("2.00")


def test_calls_monthly_totals_once_with_both_years(connect_db) -> None:
    repo = CountingRepository()
    _service(connect_db, datetime(2030, 2, 2, tzinfo=timezone.utc), repo).expense_summary()
    assert repo.monthly_calls == [(2029, 2030)]


def test_expense_summary_closes_connection_on_error(connect_db, opened) -> None:
    service = _service(connect_db, datetime(2025, 1, 1, tzinfo=timezone.utc), FailingRepository())
    with pytest.raises(sqlite3.OperationalError):
        service.expense_summary()
    _assert_all_closed(opened)


# -- recent ----------------------------------------------------------------------------- #


def _add_eleven(db: Path) -> list[int]:
    """11 Apuntes created one minute apart; mixes gastos and ingresos (16.8)."""
    ids = []
    for i in range(11):
        entry_type = "ingreso" if i % 3 == 0 else "gasto"
        ids.append(
            _add(
                db,
                date(2025, 1, 1) + timedelta(days=i),
                f"{i + 1}.00",
                entry_type=entry_type,
                created_at=BASE_CREATED + timedelta(minutes=i),
                image=f"{i:032x}.jpg",
            )
        )
    return ids


def test_recent_first_and_second_page_with_eleven_entries(db, connect_db, opened) -> None:
    ids = _add_eleven(db)
    service = _service(connect_db, datetime(2025, 6, 1, tzinfo=timezone.utc))
    newest_first = list(reversed(ids))

    first = service.recent(1)
    assert (first.page, first.page_size, first.has_more) == (1, RECENT_PAGE_SIZE, True)
    assert [e.id for e in first.items] == newest_first[:10]
    assert {e.entry_type for e in first.items} == {"gasto", "ingreso"}

    second = service.recent(2)
    assert (second.page, second.page_size, second.has_more) == (2, 10, False)
    assert [e.id for e in second.items] == newest_first[10:]
    _assert_all_closed(opened)


def test_recent_page_without_data(db, connect_db) -> None:
    service = _service(connect_db, datetime(2025, 6, 1, tzinfo=timezone.utc))
    empty = service.recent(1)
    assert empty.items == [] and empty.has_more is False and empty.page == 1

    _add_eleven(db)
    past_end = service.recent(3)
    assert past_end.items == []
    assert past_end.has_more is False
    assert past_end.page == 3


@pytest.mark.parametrize("page", [0, -1])
def test_recent_rejects_page_below_one_without_connecting(connect_db, opened, page: int) -> None:
    service = _service(connect_db, datetime(2025, 6, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        service.recent(page)
    assert opened == []


def test_recent_closes_connection_on_error(connect_db, opened) -> None:
    service = _service(connect_db, datetime(2025, 1, 1, tzinfo=timezone.utc), FailingRepository())
    with pytest.raises(sqlite3.OperationalError):
        service.recent(1)
    _assert_all_closed(opened)
