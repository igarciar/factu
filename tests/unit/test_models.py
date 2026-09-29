import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import (
    ConfirmResult,
    Draft,
    DraftFiles,
    DraftView,
    Entry,
    EntryFilter,
    EntryInput,
    ExpenseSummary,
    MonthlyComparison,
    Page,
    RecentPage,
    Totals,
    YearSummary,
)

NOW = datetime(2025, 3, 1, 10, 15, tzinfo=timezone.utc)


def _entry() -> Entry:
    return Entry(
        invoice_date=date(2025, 2, 28),
        entry_type="gasto",
        total=Decimal("121.00"),
        base_amount=Decimal("100.00"),
        vat_amount=Decimal("21.00"),
        id=1,
        ocr_text="texto",
        image_filename="0" * 32 + ".jpg",
        created_at=NOW,
        updated_at=None,
    )


def test_entry_input_defaults_optional_fields_to_none():
    entry = EntryInput(invoice_date=date(2025, 1, 1), entry_type="ingreso", total=Decimal("1.00"))
    assert entry.base_amount is None
    assert entry.vat_amount is None
    assert entry.supplier is None
    assert entry.tax_id is None
    assert entry.invoice_number is None
    assert entry.concept is None
    assert entry.category is None


def test_entry_extends_entry_input_with_keyword_fields():
    entry = _entry()
    assert isinstance(entry, EntryInput)
    assert entry.id == 1
    assert entry.updated_at is None
    with pytest.raises(TypeError):
        Entry(date(2025, 1, 1), "gasto", Decimal("1"), None, None, None, None, None, None, None, 1)  # type: ignore[misc]


def test_filter_and_draft_defaults():
    assert EntryFilter() == EntryFilter(None, None, None, None)
    draft = Draft(values={"entry_type": "gasto"}, missing=frozenset({"total"}))
    assert draft.notice is None


@pytest.mark.parametrize(
    "instance, attr",
    [
        (Draft(values={}, missing=frozenset()), "notice"),
        (EntryInput(date(2025, 1, 1), "gasto", Decimal("1")), "total"),
        (_entry(), "id"),
        (EntryFilter(), "category"),
        (Page(items=[], page=1, page_size=20, total=0), "page"),
        (Totals(expenses=Decimal("0"), income=Decimal("0")), "income"),
        (DraftFiles("a" * 32, Path("x.png"), "png", "", NOW), "ocr_text"),
        (DraftView("a" * 32, Draft(values={}, missing=frozenset()), ""), "ocr_text"),
        (ConfirmResult(), "entry_id"),
        (YearSummary(2025, Decimal("0.00")), "total"),
        (MonthlyComparison(1, "Enero", Decimal("0.00"), Decimal("0.00")), "current"),
        (RecentPage(items=[], page=1, page_size=10, has_more=False), "has_more"),
    ],
)
def test_models_are_frozen(instance, attr):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, attr, None)


@pytest.mark.parametrize(
    "total, page_size, expected",
    [(0, 20, 1), (1, 20, 1), (20, 20, 1), (21, 20, 2), (45, 20, 3), (5, 0, 1)],
)
def test_page_total_pages(total, page_size, expected):
    assert Page(items=[], page=1, page_size=page_size, total=total).total_pages == expected


def test_page_navigation_flags():
    first = Page(items=[], page=1, page_size=20, total=45)
    middle = Page(items=[], page=2, page_size=20, total=45)
    last = Page(items=[], page=3, page_size=20, total=45)
    assert (first.has_previous, first.has_next) == (False, True)
    assert (middle.has_previous, middle.has_next) == (True, True)
    assert (last.has_previous, last.has_next) == (True, False)
    empty = Page(items=[], page=1, page_size=20, total=0)
    assert (empty.has_previous, empty.has_next) == (False, False)


def test_confirm_result_saved_and_independent_defaults():
    ok = ConfirmResult(entry_id=7)
    failed = ConfirmResult(errors={"total": "Obligatorio"}, form={"total": ""})
    assert ok.saved is True
    assert failed.saved is False
    assert ok.errors == {} and ok.warnings == {} and ok.form == {}
    assert ConfirmResult().errors is not ConfirmResult().errors


def _months(numbers=range(1, 13)) -> tuple[MonthlyComparison, ...]:
    return tuple(MonthlyComparison(n, f"m{n}", Decimal("0.00"), Decimal("0.00")) for n in numbers)


def test_expense_summary_holds_twelve_months_and_is_frozen():
    summary = ExpenseSummary(
        previous=YearSummary(2024, Decimal("10.00")),
        current=YearSummary(2025, Decimal("5.50")),
        months=_months(),
    )
    assert [m.month for m in summary.months] == list(range(1, 13))
    assert (summary.previous.year, summary.current.year) == (2024, 2025)
    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.months = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "numbers",
    [range(1, 12), range(1, 14), [2, 1, *range(3, 13)], [], [1] * 12],
)
def test_expense_summary_rejects_anything_but_months_1_to_12(numbers):
    with pytest.raises(ValueError):
        ExpenseSummary(YearSummary(2024, Decimal("0")), YearSummary(2025, Decimal("0")), _months(numbers))


def test_recent_page_fields():
    page = RecentPage(items=[_entry()], page=2, page_size=10, has_more=True)
    assert (len(page.items), page.page, page.page_size, page.has_more) == (1, 2, 10, True)
