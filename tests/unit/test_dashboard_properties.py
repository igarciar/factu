"""Prueba de propiedad del Resumen_Gastos de la Portada (tarea 10.10).

``DashboardService.expense_summary()`` se compara con un modelo de referencia en memoria
que solo suma los Apuntes de Tipo ``gasto`` por año y mes. Cada ejemplo usa su propia base
de datos SQLite en un directorio temporal y cierra todas las conexiones antes de borrarlo
(en Windows los ficheros WAL abiertos impedirían la limpieza).
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models import EntryInput
from app.repository import EntryRepository, connect, init_schema
from app.services import DashboardService
from tests.strategies import MAX_DATE, MIN_DATE, amounts, dates

# El Año_Actual deja margen para fechas entre (año − 2) y (año + 1) dentro de [1900, 2099].
MIN_CURRENT_YEAR = MIN_DATE.year + 2
MAX_CURRENT_YEAR = MAX_DATE.year - 1
MAX_ENTRIES = 30
ENTRY_TYPES = ("gasto", "ingreso")
NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)  # Fecha_Alta; no influye en el resumen


@dataclass(frozen=True)
class Row:
    invoice_date: date
    entry_type: str
    total: Decimal


@st.composite
def clock_instants(draw: st.DrawFn) -> datetime:
    """Instante UTC de un año cualquiera, incluidos 1 ene 00:00 y 31 dic 23:59:59.999999."""
    year = draw(st.integers(MIN_CURRENT_YEAR, MAX_CURRENT_YEAR))
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year, 12, 31, 23, 59, 59, 999_999, tzinfo=timezone.utc)
    inside = st.datetimes(
        min_value=start.replace(tzinfo=None),
        max_value=end.replace(tzinfo=None),
        timezones=st.just(timezone.utc),
    )
    return draw(st.one_of(st.just(start), st.just(end), inside))


def dates_around(year: int) -> st.SearchStrategy[date]:
    """Fechas de factura entre (year − 2) y (year + 1), con los extremos de cada año."""

    @st.composite
    def _one(draw: st.DrawFn) -> date:
        y = year + draw(st.integers(-2, 1))
        return draw(
            st.one_of(
                st.just(date(y, 1, 1)),
                st.just(date(y, 12, 31)),
                dates(date(y, 1, 1), date(y, 12, 31)),
            )
        )

    return _one()


def rows(year: int, entry_types: st.SearchStrategy[str], max_size: int) -> st.SearchStrategy[list[Row]]:
    return st.lists(
        st.builds(Row, invoice_date=dates_around(year), entry_type=entry_types, total=amounts()),
        max_size=max_size,
    )


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[datetime, list[Row], list[Row]]:
    """(instante del Reloj, Apuntes iniciales, ingresos añadidos después); ≤ 30 Apuntes."""
    instant = draw(clock_instants())
    initial = draw(rows(instant.year, st.sampled_from(ENTRY_TYPES), MAX_ENTRIES))
    extra = draw(rows(instant.year, st.just("ingreso"), MAX_ENTRIES - len(initial)))
    return instant, initial, extra


def _insert_all(db_path: Path, repo: EntryRepository, items: list[Row], first_index: int) -> None:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for offset, row in enumerate(items):
            entry = EntryInput(
                invoice_date=row.invoice_date, entry_type=row.entry_type, total=row.total
            )
            repo.insert(conn, entry, "", f"{first_index + offset}.jpg", NOW)
        conn.commit()
    finally:
        conn.close()


def _model(items: list[Row], year: int) -> dict[int, Decimal]:
    """Suma en memoria de los gastos de ``year`` por mes (0.00 si no hay ninguno)."""
    by_month: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for row in items:
        if row.entry_type == "gasto" and row.invoice_date.year == year:
            by_month[row.invoice_date.month] += row.total
    return {month: by_month[month] for month in range(1, 13)}


def _assert_matches_model(summary, instant: datetime, items: list[Row]) -> None:
    current_year = instant.year
    previous_year = current_year - 1
    assert summary.current.year == current_year
    assert summary.previous.year == previous_year

    assert len(summary.months) == 12
    assert [m.month for m in summary.months] == list(range(1, 13))

    expected_current = _model(items, current_year)
    expected_previous = _model(items, previous_year)
    for comparison in summary.months:
        assert comparison.current == expected_current[comparison.month]
        assert comparison.previous == expected_previous[comparison.month]

    assert summary.current.total == sum(expected_current.values(), Decimal("0.00"))
    assert summary.previous.total == sum(expected_previous.values(), Decimal("0.00"))
    assert summary.current.total == sum((m.current for m in summary.months), Decimal("0.00"))
    assert summary.previous.total == sum((m.previous for m in summary.months), Decimal("0.00"))

    amounts_shown = [summary.current.total, summary.previous.total]
    amounts_shown += [m.current for m in summary.months] + [m.previous for m in summary.months]
    for amount in amounts_shown:  # siempre dos decimales, también "0.00" (16.7, 16.17)
        assert isinstance(amount, Decimal)
        assert amount.as_tuple().exponent == -2


# Feature: invoice-reader, Property 22: Resumen de gastos frente a un modelo de referencia
@settings(max_examples=100, deadline=None)
@given(scenario=scenarios())
def test_expense_summary_matches_reference_model(
    scenario: tuple[datetime, list[Row], list[Row]],
) -> None:
    """**Validates: Requirements 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.17**"""
    instant, initial, extra_incomes = scenario
    repo = EntryRepository()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "facturas.db"
        init_schema(db_path)
        service = DashboardService(repo, lambda: connect(db_path), clock=lambda: instant)

        _insert_all(db_path, repo, initial, first_index=0)
        before = service.expense_summary()
        _assert_matches_model(before, instant, initial)

        # Añadir ingresos (en los mismos años y meses) no cambia el resultado (16.5).
        _insert_all(db_path, repo, extra_incomes, first_index=len(initial))
        after = service.expense_summary()
        assert after == before
        _assert_matches_model(after, instant, initial + extra_incomes)
