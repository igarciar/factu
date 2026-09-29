"""Domain models (frozen dataclasses) shared by the core, repository, services and web layer.

Field names and types follow ``design.md`` ("Modelos en Python"). ``Totals``, ``DraftFiles``,
``DraftView`` and ``ConfirmResult`` are only named in the design's signatures; their fields are
derived from how ``ImageStore``, ``DraftService`` and ``EntryService`` use them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

EntryType = Literal["gasto", "ingreso"]
DraftNotice = Literal["ocr_failed", "no_text"]
ImageExt = Literal["jpg", "png"]


@dataclass(frozen=True)
class Draft:
    """Borrador produced by the extractor (Req. 3.1).

    ``values`` holds form-ready strings (ISO date, amounts like ``"1234.56"``); ``missing``
    lists the Campos_Apunte that were not detected (Req. 3.8).
    """

    values: dict[str, str]
    missing: frozenset[str]
    notice: DraftNotice | None = None


@dataclass(frozen=True)
class EntryInput:
    """Validated Campos_Apunte ready to be persisted (Req. 6.1)."""

    invoice_date: date
    entry_type: EntryType
    total: Decimal
    base_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    supplier: str | None = None
    tax_id: str | None = None
    invoice_number: str | None = None
    concept: str | None = None
    category: str | None = None


@dataclass(frozen=True, kw_only=True)
class Entry(EntryInput):
    """Stored Apunte. Extra fields are keyword-only because the base class has defaults."""

    id: int
    ocr_text: str
    image_filename: str
    created_at: datetime
    updated_at: datetime | None


@dataclass(frozen=True)
class EntryFilter:
    """Listing filters (Req. 8.3). ``date_from``/``date_to`` are inclusive."""

    entry_type: EntryType | None = None
    category: str | None = None
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True)
class Page(Generic[T]):
    """One page of a listing (Req. 8.2): current page, page size and total number of items."""

    items: list[T]
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        """Number of pages needed for ``total`` items; at least 1 so an empty listing has page 1."""
        if self.total <= 0 or self.page_size <= 0:
            return 1
        return -(-self.total // self.page_size)

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


@dataclass(frozen=True)
class Totals:
    """Sum of ``total`` for expenses and incomes of a filtered set (Req. 8.4)."""

    expenses: Decimal
    income: Decimal


@dataclass(frozen=True)
class DraftFiles:
    """Temporary files of a Borrador as returned by ``ImageStore.load_draft``."""

    draft_id: str
    image_path: Path
    ext: ImageExt
    ocr_text: str
    modified_at: datetime  # mtime of the temporary image, used to detect expired drafts


@dataclass(frozen=True)
class DraftView:
    """What the review page needs: the draft id (image URL), the Borrador and the Texto_OCR."""

    draft_id: str
    draft: Draft
    ocr_text: str


@dataclass(frozen=True)
class ConfirmResult:
    """Outcome of ``EntryService.confirm``.

    On success ``entry_id`` is set. Otherwise ``errors``/``warnings`` explain why and ``form``
    keeps the values the Usuario sent so the form can be redisplayed (Req. 5.7).
    """

    entry_id: int | None = None
    errors: dict[str, str] = field(default_factory=dict)
    warnings: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] = field(default_factory=dict)

    @property
    def saved(self) -> bool:
        return self.entry_id is not None


# Portada (Req. 16)
@dataclass(frozen=True)
class YearSummary:
    """Sum of the expenses of one natural year (Req. 16.3, 16.4)."""

    year: int
    total: Decimal  # 2 decimals


@dataclass(frozen=True)
class MonthlyComparison:
    """Expenses of one month in the previous year and in the Año_Actual (Req. 16.6)."""

    month: int  # 1..12
    name: str  # MONTH_NAMES_ES[month - 1]
    previous: Decimal  # 0.00 when there are no expenses
    current: Decimal  # 0.00 when there are no expenses


_MONTHS = tuple(range(1, 13))


@dataclass(frozen=True)
class ExpenseSummary:
    """Annual totals plus the month-by-month comparison shown on the Portada (Req. 16.3-16.6)."""

    previous: YearSummary
    current: YearSummary
    months: tuple[MonthlyComparison, ...]  # exactly 12, months 1..12 in order

    def __post_init__(self) -> None:
        if tuple(m.month for m in self.months) != _MONTHS:
            raise ValueError("ExpenseSummary.months must hold exactly months 1..12 in order")


@dataclass(frozen=True)
class RecentPage:
    """One Página_Recientes of Últimas_Facturas (Req. 16.8, 16.10).

    It carries no ``total``: ``has_more`` comes from fetching ``page_size + 1`` rows.
    """

    items: list[Entry]  # at most page_size
    page: int  # >= 1
    page_size: int  # 10
    has_more: bool  # there are Apuntes after the last one in items
