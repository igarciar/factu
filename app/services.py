"""Application services: the use cases the web layer calls.

- ``DraftService``: upload → temporary files + OCR + Borrador; reload and cancel (Req. 2, 4).
- ``DraftPurger``: opportunistic purge of expired Borradores, at most once per ``interval``
  and process, without background threads (Req. 4.6).
- ``EntryService`` (task 10.2) and ``DashboardService`` (task 10.9) are appended below.

Requirements: 2.3, 2.4, 2.5, 4.1, 4.4, 4.5, 4.6.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import extractor
from app.extractor import FIELDS
from app.formatting import MONTH_NAMES_ES
from app.models import (
    ConfirmResult,
    Draft,
    DraftFiles,
    DraftNotice,
    DraftView,
    Entry,
    EntryFilter,
    EntryInput,
    ExpenseSummary,
    ImageExt,
    MonthlyComparison,
    Page,
    RecentPage,
    Totals,
    YearSummary,
)
from app.ocr import OcrEngine, OcrError, OcrTimeout, OcrUnavailable
from app.repository import CategoryRepository, EntryRepository, cents_to_decimal
from app.storage import ImageStore
from app.validation import ValidationResult, validate_entry

logger = logging.getLogger(__name__)

DEFAULT_DRAFT_TTL = timedelta(hours=24)
DEFAULT_PURGE_INTERVAL = timedelta(hours=1)
DEFAULT_ENTRY_TYPE = "gasto"


def utc_now() -> datetime:
    """Default Reloj: the current instant in UTC (aware)."""
    return datetime.now(timezone.utc)


def empty_draft(notice: DraftNotice | None = None) -> Draft:
    """Borrador with every Campo_Apunte blank except ``entry_type="gasto"`` (Req. 2.3-2.5, 3.9).

    Every field but ``entry_type`` is listed as not detected, as ``extractor.extract`` would.
    """
    values = {name: "" for name in FIELDS}
    values["entry_type"] = DEFAULT_ENTRY_TYPE
    missing = frozenset(name for name in FIELDS if name != "entry_type")
    return Draft(values=values, missing=missing, notice=notice)


def is_draft_expired(files: DraftFiles, now: datetime, ttl: timedelta) -> bool:
    """``True`` if the Borrador image is older than ``ttl`` at ``now`` (same rule as the purge).

    Compared as POSIX timestamps so aware and naive ``now`` values behave like
    ``ImageStore.purge_expired_drafts``.
    """
    return files.modified_at.timestamp() < (now - ttl).timestamp()


class DraftService:
    """Creates, reloads and discards Borradores. Never writes to the database (Req. 4.4)."""

    def __init__(
        self,
        store: ImageStore,
        ocr: OcrEngine,
        clock: Callable[[], datetime] = utc_now,
        ttl: timedelta = DEFAULT_DRAFT_TTL,
    ) -> None:
        self.store = store
        self.ocr = ocr
        self.clock = clock
        self.ttl = ttl

    def create(self, data: bytes, fmt: ImageExt) -> DraftView:
        """Store the upload as a Borrador, run the OCR and build the Borrador to review.

        ``data`` must already be validated (``uploads.validate_upload``) and ``fmt`` is the
        detected format. OCR is a plain blocking call. Any ``OcrError`` (timeout, Tesseract or
        ``spa`` missing, undecodable image) is logged and yields an empty Borrador with
        ``notice="ocr_failed"``, so an upload never ends in a 500 because of the OCR.
        """
        draft_id = uuid4().hex
        self.store.save_temp(draft_id, data, fmt)
        text = ""
        try:
            text = self.ocr.extract_text(data)
        except (OcrTimeout, OcrUnavailable) as exc:
            logger.error("Fallo del OCR en el borrador %s (%s): %s", draft_id, type(exc).__name__, exc)
            draft = empty_draft("ocr_failed")
        except OcrError as exc:
            logger.error("Error del OCR en el borrador %s: %s", draft_id, exc)
            draft = empty_draft("ocr_failed")
        else:
            draft = self._draft_from_text(text, blank_notice="no_text")
        self.store.save_temp_text(draft_id, text)
        return DraftView(draft_id=draft_id, draft=draft, ocr_text=text)

    def get(self, draft_id: str) -> DraftView | None:
        """Reload a Borrador for the review page; ``None`` if missing, malformed or expired.

        The Borrador is rebuilt with ``extractor.extract`` from the stored Texto_OCR (the
        extractor is deterministic, Req. 3.10). With a blank text the result is an empty
        Borrador without notice: the upload-time notice is not persisted.
        """
        files = self.load_files(draft_id)
        if files is None:
            return None
        draft = self._draft_from_text(files.ocr_text, blank_notice=None)
        return DraftView(draft_id=files.draft_id, draft=draft, ocr_text=files.ocr_text)

    def load_files(self, draft_id: str) -> DraftFiles | None:
        """``ImageStore.load_draft`` that also treats Borradores older than ``ttl`` as absent."""
        files = self.store.load_draft(draft_id)
        if files is None or is_draft_expired(files, self.clock(), self.ttl):
            return None
        return files

    def cancel(self, draft_id: str) -> None:
        """Discard every temporary file of the Borrador (Req. 4.5). Unknown ids are a no-op."""
        self.store.discard_draft(draft_id)

    @staticmethod
    def _draft_from_text(text: str, blank_notice: DraftNotice | None) -> Draft:
        if not text.strip():
            return empty_draft(blank_notice)
        return extractor.extract(text)


class DraftPurger:
    """Deletes expired Borrador files at most once per ``interval`` in this process (Req. 4.6).

    ``maybe_purge`` is called at the start of ``GET /``, ``GET /upload`` and ``POST /uploads``.
    The interval is measured with ``monotonic`` so wall-clock changes do not affect it; the
    expiry itself uses ``clock``.
    """

    def __init__(
        self,
        store: ImageStore,
        ttl: timedelta = DEFAULT_DRAFT_TTL,
        interval: timedelta = DEFAULT_PURGE_INTERVAL,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.ttl = ttl
        self.interval = interval
        self.clock = clock
        self.monotonic = monotonic
        self._last_purge: float | None = None

    def purge_now(self) -> list[str]:
        """Purge immediately and reset the interval mark. Errors propagate to the caller."""
        self._last_purge = self.monotonic()
        return self.store.purge_expired_drafts(self.clock(), self.ttl)

    def maybe_purge(self) -> list[str]:
        """Purge if ``interval`` has elapsed since the last purge (or none yet); else ``[]``.

        Never raises: an unexpected failure is logged with ``logger.exception`` so the request
        goes on. The mark is updated before purging, so a failing purge is not retried on every
        request.
        """
        now = self.monotonic()
        if self._last_purge is not None and now - self._last_purge < self.interval.total_seconds():
            return []
        try:
            return self.purge_now()
        except Exception:
            logger.exception("Error purgando borradores caducados")
            return []


# --------------------------------------------------------------------------------------- #
# EntryService (task 10.2) — Req. 5.7, 6.1-6.5, 7.3, 9.1, 9.3, 9.4
# --------------------------------------------------------------------------------------- #

#: Name of the "Confirmo guardar con descuadre" checkbox in the review and edit forms (5.5).
MISMATCH_FIELD = "confirm_mismatch"
_CHECKBOX_ON = frozenset({"1", "on", "true", "yes"})


class ServiceError(Exception):
    """Base class of the errors ``EntryService`` raises to the web layer."""


class NotFound(ServiceError, LookupError):
    """The Borrador or Apunte does not exist (the web layer answers 404)."""


class DraftNotFound(NotFound):
    """Borrador missing, malformed or expired (Req. 4.6)."""


class EntryNotFound(NotFound):
    """Apunte missing (Req. 8.6)."""


class SaveError(ServiceError):
    """Saving failed and was undone (Req. 6.5); the web layer answers 500 and lets the Usuario retry."""


def is_mismatch_confirmed(form: Mapping[str, str]) -> bool:
    """``True`` if the form carries the ``MISMATCH_FIELD`` checkbox ticked (browsers send ``"on"``)."""
    value = form.get(MISMATCH_FIELD)
    return value is not None and value.strip().lower() in _CHECKBOX_ON


def _form_values(form: Mapping[str, str]) -> dict[str, str]:
    """Copy of the submitted values to redisplay the form (Req. 5.7).

    ``items()`` of a Werkzeug ``MultiDict`` yields the first value of each key.
    """
    return {str(key): str(value) for key, value in form.items()}


def _rejected(result: ValidationResult, form: Mapping[str, str]) -> ConfirmResult:
    return ConfirmResult(
        errors=dict(result.errors), warnings=dict(result.warnings), form=_form_values(form)
    )


def _rollback_quietly(conn: sqlite3.Connection | None) -> None:
    """Roll back if a transaction is open; a failing rollback is logged, not raised.

    Used inside ``except`` blocks so the original error is the one reported.
    """
    if conn is None:
        return
    try:
        if conn.in_transaction:
            conn.rollback()
    except Exception:
        logger.exception("Error deshaciendo la transacción")


class EntryService:
    """Confirms Borradores into Apuntes and edits, deletes and reads Apuntes.

    Every method opens a short-lived connection with ``connect`` and always closes it.
    Writes run inside ``BEGIN IMMEDIATE`` (the connection has ``isolation_level=None``).
    ``clock`` gives the Fecha_Alta/``updated_at`` and, with ``ttl``, decides whether a
    Borrador has expired, with the same rule as ``DraftService.load_files``.
    """

    def __init__(
        self,
        store: ImageStore,
        connect: Callable[[], sqlite3.Connection],
        entries: EntryRepository | None = None,
        categories: CategoryRepository | None = None,
        clock: Callable[[], datetime] = utc_now,
        ttl: timedelta = DEFAULT_DRAFT_TTL,
    ) -> None:
        self.store = store
        self.connect = connect
        self.entries = entries if entries is not None else EntryRepository()
        self.categories = categories if categories is not None else CategoryRepository()
        self.clock = clock
        self.ttl = ttl

    # -- Alta ---------------------------------------------------------------------------

    def confirm(
        self, draft_id: str, form: Mapping[str, str], mismatch_confirmed: bool
    ) -> ConfirmResult:
        """Save the Borrador as an Apunte and move its image to the store (Req. 6.1-6.5).

        Raises ``DraftNotFound`` if the Borrador is missing or expired. A form that does not
        validate (or has an unconfirmed mismatch) yields a ``ConfirmResult`` with the errors,
        the warnings and the submitted values, and nothing is written (Req. 5.7).

        Database row, category and image move are all-or-nothing: on any failure the
        transaction is rolled back, the image is moved back to ``tmp/``, the error is logged
        and ``SaveError`` is raised. The Borrador stays available so the Usuario can retry.
        """
        files = self._load_draft(draft_id)
        if files is None:
            raise DraftNotFound(draft_id)
        result = validate_entry(form, mismatch_confirmed=mismatch_confirmed)
        if result.cleaned is None:
            return _rejected(result, form)

        entry_id = self._save_new(files, result.cleaned)
        try:
            self.store.discard_draft_text(draft_id)
        except OSError:
            # The Apunte is saved; the leftover text is removed by the purge (Req. 4.6).
            logger.exception("Error borrando el texto temporal del borrador %s", draft_id)
        return ConfirmResult(entry_id=entry_id, warnings=dict(result.warnings))

    def _load_draft(self, draft_id: str) -> DraftFiles | None:
        files = self.store.load_draft(draft_id)
        if files is None or is_draft_expired(files, self.clock(), self.ttl):
            return None
        return files

    def _save_new(self, files: DraftFiles, cleaned: EntryInput) -> int:
        draft_id = files.draft_id
        conn: sqlite3.Connection | None = None
        final_name: str | None = None
        try:
            conn = self.connect()
            conn.execute("BEGIN IMMEDIATE")
            if cleaned.category:
                self.categories.ensure(conn, cleaned.category)  # Req. 7.3
            final_name = self.store.promote(draft_id)  # Req. 6.2, 6.3
            entry_id = self.entries.insert(
                conn, cleaned, files.ocr_text, final_name, self.clock()
            )
            conn.commit()
        except Exception as exc:
            _rollback_quietly(conn)
            if final_name is not None:
                self._demote_quietly(final_name, draft_id)
            logger.exception("Error guardando el apunte del borrador %s", draft_id)
            raise SaveError(draft_id) from exc
        finally:
            if conn is not None:
                conn.close()
        return entry_id

    def _demote_quietly(self, final_name: str, draft_id: str) -> None:
        try:
            self.store.demote(final_name, draft_id)
        except Exception:
            logger.exception(
                "Error devolviendo la imagen %s al borrador %s", final_name, draft_id
            )

    # -- Edición y borrado ---------------------------------------------------------------

    def update(
        self, entry_id: int, form: Mapping[str, str], mismatch_confirmed: bool
    ) -> ConfirmResult:
        """Validate like ``confirm`` and replace the Campos_Apunte of ``entry_id`` (Req. 9.1).

        Raises ``EntryNotFound`` if the Apunte does not exist (checked before validating, so
        an invalid form for a missing id is still a 404). Invalid forms yield a
        ``ConfirmResult`` with errors and the submitted values. A new category is added to
        the suggestions (Req. 7.3). A failing write is rolled back, logged and raised as
        ``SaveError``.
        """
        conn = self.connect()
        try:
            if self.entries.get(conn, entry_id) is None:
                raise EntryNotFound(entry_id)
            result = validate_entry(form, mismatch_confirmed=mismatch_confirmed)
            cleaned = result.cleaned
            if cleaned is None:
                return _rejected(result, form)
            try:
                conn.execute("BEGIN IMMEDIATE")
                if cleaned.category:
                    self.categories.ensure(conn, cleaned.category)
                updated = self.entries.update(conn, entry_id, cleaned, self.clock())
                if updated:
                    conn.commit()
                else:
                    conn.rollback()
            except Exception as exc:
                _rollback_quietly(conn)
                logger.exception("Error actualizando el apunte %s", entry_id)
                raise SaveError(entry_id) from exc
        finally:
            conn.close()
        if not updated:  # deleted by another request between get and update
            raise EntryNotFound(entry_id)
        return ConfirmResult(entry_id=entry_id, warnings=dict(result.warnings))

    def delete(self, entry_id: int) -> Entry:
        """Delete the Apunte and then its image (Req. 9.3); return the deleted Apunte.

        Raises ``EntryNotFound`` if it does not exist. The image is removed after the
        ``commit``: if it was already missing a warning is logged and the deletion still
        succeeds (Req. 9.4); if removing it fails the orphan file is logged.
        """
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                deleted = self.entries.delete(conn, entry_id)
                if deleted is None:
                    conn.rollback()
                else:
                    conn.commit()
            except BaseException:
                _rollback_quietly(conn)
                raise
        finally:
            conn.close()
        if deleted is None:
            raise EntryNotFound(entry_id)

        name = deleted.image_filename
        try:
            removed = self.store.delete(name)
        except OSError:
            logger.exception("No se ha podido borrar la imagen %s del apunte %s", name, entry_id)
            return deleted
        if not removed:
            logger.warning("La imagen %s del apunte %s no existía al borrarlo", name, entry_id)
        return deleted

    # -- Lectura ---------------------------------------------------------------------------

    def get(self, entry_id: int) -> Entry | None:
        """The Apunte, or ``None`` if it does not exist (the web layer answers 404, Req. 8.6)."""
        conn = self.connect()
        try:
            return self.entries.get(conn, entry_id)
        finally:
            conn.close()

    def list(self, flt: EntryFilter, page: int) -> Page[Entry]:
        """One page (20 Apuntes) of the filtered listing (Req. 8.1-8.3)."""
        conn = self.connect()
        try:
            return self.entries.list(conn, flt, page)
        finally:
            conn.close()

    def totals(self, flt: EntryFilter) -> Totals:
        """Sums of expenses and incomes of the filtered Apuntes (Req. 8.4)."""
        conn = self.connect()
        try:
            return self.entries.totals(conn, flt)
        finally:
            conn.close()

    def category_names(self) -> list[str]:
        """Category suggestions for the ``<datalist>`` (Req. 7.1-7.3)."""
        conn = self.connect()
        try:
            return self.categories.list(conn)
        finally:
            conn.close()


# --------------------------------------------------------------------------------------- #
# DashboardService (task 10.9) — Req. 16.2-16.8, 16.14, 16.17
# --------------------------------------------------------------------------------------- #

#: Apuntes per Página_Recientes of Últimas_Facturas (Req. 16.8).
RECENT_PAGE_SIZE = 10


class DashboardService:
    """Read-only data of the Portada: Resumen_Gastos and Últimas_Facturas (Req. 16).

    Every method opens a short-lived connection with ``connect`` and always closes it.
    ``clock`` decides the Año_Actual (its ``year``, in the clock's own time zone).
    """

    def __init__(
        self,
        entries: EntryRepository,
        connect: Callable[[], sqlite3.Connection],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.entries = entries
        self.connect = connect
        self.clock = clock

    def expense_summary(self) -> ExpenseSummary:
        """Expenses of the Año_Actual and the previous year, per year and per month.

        Año_Actual is ``clock().year`` and the previous year is one less (16.2). One call to
        ``monthly_expense_totals`` covers both years; incomes are excluded there (16.5).
        Months without expenses count as 0 (16.7, 16.17). Annual totals are the integer sum
        of the 12 monthly cents of each year, converted to ``Decimal`` afterwards (16.3, 16.4).
        """
        current_year = self.clock().year
        previous_year = current_year - 1
        conn = self.connect()
        try:
            cents = self.entries.monthly_expense_totals(conn, previous_year, current_year)
        finally:
            conn.close()

        previous_cents = [cents.get((previous_year, month), 0) for month in range(1, 13)]
        current_cents = [cents.get((current_year, month), 0) for month in range(1, 13)]
        months = tuple(
            MonthlyComparison(
                month=month,
                name=MONTH_NAMES_ES[month - 1],
                previous=cents_to_decimal(previous_cents[month - 1]),
                current=cents_to_decimal(current_cents[month - 1]),
            )
            for month in range(1, 13)
        )
        return ExpenseSummary(
            previous=YearSummary(previous_year, cents_to_decimal(sum(previous_cents))),
            current=YearSummary(current_year, cents_to_decimal(sum(current_cents))),
            months=months,
        )

    def recent(self, page: int) -> RecentPage:
        """Página_Recientes ``page`` of Últimas_Facturas, both entry types (16.8, 16.10, 16.14).

        ``page`` must already be normalized (``formatting.parse_recent_page``); a value < 1
        raises ``ValueError`` without touching the database. A page past the end yields
        ``items == []`` and ``has_more is False`` (16.16).
        """
        if page < 1:
            raise ValueError(f"page must be >= 1, got {page}")
        offset = (page - 1) * RECENT_PAGE_SIZE
        conn = self.connect()
        try:
            items, has_more = self.entries.recent(conn, offset, RECENT_PAGE_SIZE)
        finally:
            conn.close()
        return RecentPage(items=items, page=page, page_size=RECENT_PAGE_SIZE, has_more=has_more)
