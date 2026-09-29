"""SQLite persistence: connection helper, schema and repositories.

Requirements: 6.1, 6.4, 7.1, 7.3, 8.1-8.4, 9.1, 9.3, 10.5, 14.6, 16.3-16.16.

Connections are opened per request with ``isolation_level=None`` so that the
callers control transactions explicitly (``BEGIN IMMEDIATE`` / ``commit`` /
``rollback``). Repository methods never commit. Every query uses ``?``
placeholders; user values are never interpolated into SQL.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from os import PathLike

from app.models import Entry, EntryFilter, EntryInput, Page, Totals

#: Seconds a connection waits for a lock held by another Gunicorn worker.
BUSY_TIMEOUT_SECONDS = 5
_WAL_RETRY_SECONDS = 0.05

#: Initial category suggestions (Req. 7.1).
SEED_CATEGORIES: tuple[str, ...] = (
    "Suministros",
    "Alimentación",
    "Transporte",
    "Hogar",
    "Salud",
    "Ocio",
    "Servicios profesionales",
    "Impuestos",
    "Otros",
)

# Fixed DDL statements (design "Modelo de datos"). Executed one by one inside
# the ``BEGIN IMMEDIATE`` transaction; ``executescript`` is avoided because it
# issues an implicit COMMIT.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS entries (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_date     TEXT    NOT NULL,
        supplier         TEXT,
        tax_id           TEXT,
        invoice_number   TEXT,
        concept          TEXT,
        category         TEXT,
        entry_type       TEXT    NOT NULL CHECK (entry_type IN ('gasto','ingreso')),
        base_cents       INTEGER CHECK (base_cents  IS NULL OR base_cents  >= 0),
        vat_cents        INTEGER CHECK (vat_cents   IS NULL OR vat_cents   >= 0),
        total_cents      INTEGER NOT NULL CHECK (total_cents >= 0),
        ocr_text         TEXT    NOT NULL DEFAULT '',
        image_filename   TEXT    NOT NULL UNIQUE,
        created_at       TEXT    NOT NULL,
        updated_at       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_entries_date ON entries(invoice_date DESC, id DESC)",
    # Portada (Req. 16)
    "CREATE INDEX IF NOT EXISTS ix_entries_created ON entries(created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_entries_type_date ON entries(entry_type, invoice_date)",
    """
    CREATE TABLE IF NOT EXISTS categories (
        name TEXT PRIMARY KEY COLLATE NOCASE
    )
    """,
)

_INSERT_CATEGORY = "INSERT OR IGNORE INTO categories (name) VALUES (?)"


def connect(db_path: str | PathLike[str]) -> sqlite3.Connection:
    """Open a short-lived connection configured for multi-process use.

    - ``timeout=5``: wait for locks instead of failing with ``database is locked``.
    - ``isolation_level=None``: no implicit transactions; callers issue
      ``BEGIN IMMEDIATE`` for writes and finish with ``commit``/``rollback``.
    - ``PRAGMA foreign_keys=ON`` and ``journal_mode=WAL``.
    """
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        _enable_wal(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch to WAL (persistent in the file) if the database is not already in WAL.

    Changing the journal mode needs an exclusive lock and SQLite may report
    ``database is locked`` immediately, without calling the busy handler, when
    another process is doing the same switch. Retry within the busy timeout.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_SECONDS
    while True:
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
            return
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(_WAL_RETRY_SECONDS)


def init_schema(db_path: str | PathLike[str]) -> None:
    """Create tables, indexes and seed categories if they do not exist (Req. 10.5, 7.1).

    Runs inside ``BEGIN IMMEDIATE`` so that several processes starting at the
    same time serialise on the write lock; every statement is idempotent
    (``IF NOT EXISTS`` / ``INSERT OR IGNORE``).
    """
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.executemany(_INSERT_CATEGORY, [(name,) for name in SEED_CATEGORIES])
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()


class CategoryRepository:
    """Category suggestions (Req. 7.1, 7.2, 7.3). Names are unique ignoring case."""

    def list(self, conn: sqlite3.Connection) -> list[str]:
        """Return all category names sorted alphabetically (case-insensitive)."""
        rows = conn.execute("SELECT name FROM categories ORDER BY name COLLATE NOCASE, name")
        return [row[0] for row in rows]

    def ensure(self, conn: sqlite3.Connection, name: str) -> None:
        """Add ``name`` (stripped) if no category with the same name ignoring case exists.

        Case is compared with ``str.casefold()`` in Python because SQLite's
        ``NOCASE`` only folds ASCII ('Ñ'/'ñ', 'Ó'/'ó' or 'ß'/'SS' would be
        different names). The category table is small, so scanning it is cheap.
        Blank names are ignored. Runs within the caller's transaction, if any
        (the caller's ``BEGIN IMMEDIATE`` serialises the check and the insert).
        """
        cleaned = name.strip()
        if not cleaned:
            return
        folded = cleaned.casefold()
        for (existing,) in conn.execute("SELECT name FROM categories"):
            if existing.casefold() == folded:
                return
        conn.execute(_INSERT_CATEGORY, (cleaned,))


# --------------------------------------------------------------------------- #
# Amounts and timestamps
# --------------------------------------------------------------------------- #

_CENT = Decimal("0.01")


def decimal_to_cents(amount: Decimal) -> int:
    """Convert an amount with at most two decimals into integer cents.

    Raises ``ValueError`` if ``amount`` has a non-zero third decimal (the
    validation layer quantizes amounts, so this never rounds silently).
    """
    quantized = amount.quantize(_CENT)
    if quantized != amount:
        raise ValueError(f"amount has more than two decimals: {amount}")
    return int(quantized.scaleb(2))


def cents_to_decimal(cents: int) -> Decimal:
    """Convert integer cents into a ``Decimal`` with exactly two decimals."""
    return Decimal(cents).scaleb(-2)


def _optional_cents(amount: Decimal | None) -> int | None:
    return None if amount is None else decimal_to_cents(amount)


def _optional_decimal(cents: int | None) -> Decimal | None:
    return None if cents is None else cents_to_decimal(cents)


def format_timestamp(now: datetime) -> str:
    """UTC ISO 8601 with fixed microsecond precision, so text order is chronological.

    Example: ``2025-03-01T10:15:00.123456+00:00``. A naive ``now`` is interpreted
    as local time (``datetime.astimezone`` semantics).
    """
    return now.astimezone(timezone.utc).isoformat(timespec="microseconds")


# --------------------------------------------------------------------------- #
# EntryRepository
# --------------------------------------------------------------------------- #

# Fixed column list shared by every SELECT; the order matches ``_row_to_entry``.
_ENTRY_COLUMNS = (
    "id, invoice_date, supplier, tax_id, invoice_number, concept, category, entry_type, "
    "base_cents, vat_cents, total_cents, ocr_text, image_filename, created_at, updated_at"
)

_INSERT_ENTRY = (
    "INSERT INTO entries (invoice_date, supplier, tax_id, invoice_number, concept, category, "
    "entry_type, base_cents, vat_cents, total_cents, ocr_text, image_filename, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_UPDATE_ENTRY = (
    "UPDATE entries SET invoice_date = ?, supplier = ?, tax_id = ?, invoice_number = ?, "
    "concept = ?, category = ?, entry_type = ?, base_cents = ?, vat_cents = ?, total_cents = ?, "
    "updated_at = ? WHERE id = ?"
)

_SELECT_ENTRY = f"SELECT {_ENTRY_COLUMNS} FROM entries WHERE id = ?"
_DELETE_ENTRY = f"DELETE FROM entries WHERE id = ? RETURNING {_ENTRY_COLUMNS}"

#: Default page size of the listing (Req. 8.2).
DEFAULT_PAGE_SIZE = 20


def _entry_params(entry: EntryInput) -> tuple[object, ...]:
    """Values of the Campos_Apunte in the column order used by insert/update."""
    return (
        entry.invoice_date.isoformat(),
        entry.supplier,
        entry.tax_id,
        entry.invoice_number,
        entry.concept,
        entry.category,
        entry.entry_type,
        _optional_cents(entry.base_amount),
        _optional_cents(entry.vat_amount),
        decimal_to_cents(entry.total),
    )


def _row_to_entry(row: sqlite3.Row | tuple) -> Entry:
    (
        entry_id, invoice_date, supplier, tax_id, invoice_number, concept, category,
        entry_type, base_cents, vat_cents, total_cents, ocr_text, image_filename,
        created_at, updated_at,
    ) = row
    return Entry(
        id=entry_id,
        invoice_date=date.fromisoformat(invoice_date),
        entry_type=entry_type,
        total=cents_to_decimal(total_cents),
        base_amount=_optional_decimal(base_cents),
        vat_amount=_optional_decimal(vat_cents),
        supplier=supplier,
        tax_id=tax_id,
        invoice_number=invoice_number,
        concept=concept,
        category=category,
        ocr_text=ocr_text,
        image_filename=image_filename,
        created_at=datetime.fromisoformat(created_at),
        updated_at=None if updated_at is None else datetime.fromisoformat(updated_at),
    )


def _where(flt: EntryFilter) -> tuple[str, list[object]]:
    """Build the ``WHERE`` clause from fixed fragments plus a parameter list (Req. 8.3, 14.6).

    Filters combine with ``AND``; the date range is inclusive. Returns ``("", [])``
    when no filter is set.
    """
    fragments: list[str] = []
    params: list[object] = []
    if flt.entry_type is not None:
        fragments.append("entry_type = ?")
        params.append(flt.entry_type)
    if flt.category is not None:
        fragments.append("category = ?")
        params.append(flt.category)
    if flt.date_from is not None:
        fragments.append("invoice_date >= ?")
        params.append(flt.date_from.isoformat())
    if flt.date_to is not None:
        fragments.append("invoice_date <= ?")
        params.append(flt.date_to.isoformat())
    if not fragments:
        return "", params
    return " WHERE " + " AND ".join(fragments), params


class EntryRepository:
    """Apuntes stored in ``entries``. Amounts are integer cents in SQLite, ``Decimal`` in Python.

    Every method runs on the caller's connection and transaction; none commits.
    """

    def insert(
        self,
        conn: sqlite3.Connection,
        entry: EntryInput,
        ocr_text: str,
        image_filename: str,
        now: datetime,
    ) -> int:
        """Insert an Apunte and return its id (Req. 6.1, 6.4).

        ``created_at`` is ``now`` in UTC with a fixed format (see ``format_timestamp``).
        A duplicated ``image_filename`` raises ``sqlite3.IntegrityError``.
        """
        cursor = conn.execute(
            _INSERT_ENTRY,
            (*_entry_params(entry), ocr_text, image_filename, format_timestamp(now)),
        )
        return int(cursor.lastrowid)

    def update(
        self, conn: sqlite3.Connection, entry_id: int, entry: EntryInput, now: datetime
    ) -> bool:
        """Replace the Campos_Apunte of ``entry_id`` and set ``updated_at`` (Req. 9.1).

        ``ocr_text``, ``image_filename`` and ``created_at`` are kept. Returns
        ``False`` if the Apunte does not exist.
        """
        cursor = conn.execute(
            _UPDATE_ENTRY, (*_entry_params(entry), format_timestamp(now), entry_id)
        )
        return cursor.rowcount == 1

    def get(self, conn: sqlite3.Connection, entry_id: int) -> Entry | None:
        """Return the Apunte ``entry_id`` or ``None`` if it does not exist."""
        row = conn.execute(_SELECT_ENTRY, (entry_id,)).fetchone()
        return None if row is None else _row_to_entry(row)

    def delete(self, conn: sqlite3.Connection, entry_id: int) -> Entry | None:
        """Delete the Apunte and return it (so the caller can remove its image, Req. 9.3).

        Returns ``None`` if it does not exist.
        """
        row = conn.execute(_DELETE_ENTRY, (entry_id,)).fetchone()
        return None if row is None else _row_to_entry(row)

    def list(
        self,
        conn: sqlite3.Connection,
        flt: EntryFilter,
        page: int,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Page[Entry]:
        """One page of the filtered listing ordered by ``(invoice_date DESC, id DESC)`` (Req. 8.1-8.3).

        ``page`` starts at 1. A page after the last one returns no items but the
        real ``total``. Raises ``ValueError`` if ``page`` or ``page_size`` is < 1.
        """
        if page < 1:
            raise ValueError(f"page must be >= 1, got {page}")
        if page_size < 1:
            raise ValueError(f"page_size must be >= 1, got {page_size}")
        where, params = _where(flt)
        total = conn.execute(f"SELECT COUNT(*) FROM entries{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_ENTRY_COLUMNS} FROM entries{where} "
            "ORDER BY invoice_date DESC, id DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return Page(
            items=[_row_to_entry(row) for row in rows],
            page=page,
            page_size=page_size,
            total=int(total),
        )

    def totals(self, conn: sqlite3.Connection, flt: EntryFilter) -> Totals:
        """Sum of ``total`` of expenses and of incomes matching ``flt`` (Req. 8.4)."""
        where, params = _where(flt)
        expenses, income = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN entry_type = ? THEN total_cents END), 0), "
            "COALESCE(SUM(CASE WHEN entry_type = ? THEN total_cents END), 0) "
            f"FROM entries{where}",
            ["gasto", "ingreso", *params],
        ).fetchone()
        return Totals(expenses=cents_to_decimal(expenses), income=cents_to_decimal(income))

    # ------------------------------------------------------------------ #
    # Portada (Req. 16)
    # ------------------------------------------------------------------ #

    def monthly_expense_totals(
        self, conn: sqlite3.Connection, first_year: int, last_year: int
    ) -> dict[tuple[int, int], int]:
        """Sum of ``total_cents`` of expenses per ``(year, month)`` in ``[first_year, last_year]``.

        Only months with at least one expense appear; the service fills the rest
        with 0 (Req. 16.3-16.7). Incomes are excluded (16.5). The ISO text range
        on ``invoice_date`` uses ``ix_entries_type_date``; every value is a ``?``
        parameter (14.6). Raises ``ValueError`` if ``first_year > last_year`` or
        the range is outside ``1..9998`` (the exclusive upper bound must stay a
        four-digit year so the text comparison is valid).
        """
        if first_year > last_year:
            raise ValueError(f"first_year {first_year} > last_year {last_year}")
        if first_year < 1 or last_year > 9998:
            raise ValueError(f"year range out of bounds: {first_year}..{last_year}")
        rows = conn.execute(
            "SELECT CAST(strftime('%Y', invoice_date) AS INTEGER) AS y, "
            "CAST(strftime('%m', invoice_date) AS INTEGER) AS m, "
            "SUM(total_cents) AS cents "
            "FROM entries "
            "WHERE entry_type = ? AND invoice_date >= ? AND invoice_date < ? "
            "GROUP BY y, m",
            ("gasto", f"{first_year:04d}-01-01", f"{last_year + 1:04d}-01-01"),
        )
        return {(int(y), int(m)): int(cents) for y, m, cents in rows}

    def recent(
        self, conn: sqlite3.Connection, offset: int, limit: int = 10
    ) -> tuple[list[Entry], bool]:
        """Block of Apuntes of both types ordered by ``(created_at DESC, id DESC)`` (Req. 16.8).

        Fetches ``limit + 1`` rows: ``has_more`` is ``True`` when there are Apuntes
        after the last returned one (16.10, 16.14). An ``offset`` past the end
        returns ``([], False)`` (16.16). Raises ``ValueError`` if ``offset < 0``
        or ``limit < 1``.
        """
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset}")
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        rows = conn.execute(
            f"SELECT {_ENTRY_COLUMNS} FROM entries "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit + 1, offset),
        ).fetchall()
        has_more = len(rows) > limit
        return [_row_to_entry(row) for row in rows[:limit]], has_more
