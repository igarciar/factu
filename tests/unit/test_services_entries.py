"""Unit tests for EntryService (Req. 5.7, 6.1-6.5, 7.3, 9.1, 9.3, 9.4)."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import Settings
from app.models import EntryFilter, EntryInput
from app.repository import EntryRepository, connect, init_schema
from app.services import (
    MISMATCH_FIELD,
    DraftNotFound,
    EntryNotFound,
    EntryService,
    SaveError,
    is_mismatch_confirmed,
)
from app.storage import UUID_NAME, ImageStore
from app.uploads import JPEG_MAGIC, PNG_MAGIC

JPEG = JPEG_MAGIC + bytes(range(256)) + b"\r\n\x00\n\r"
PNG = PNG_MAGIC + b"\x00\xff\r\n" * 16
NOW = datetime(2025, 3, 2, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=2)
TTL = timedelta(hours=24)
OCR_TEXT = "Ferretería López\nTotal 121,00 €\n"
DRAFT_A = "a" * 32
DRAFT_B = "b" * 32

VALID_FORM = {
    "invoice_date": "2025-03-01",
    "entry_type": "gasto",
    "base_amount": "100.00",
    "vat_amount": "21.00",
    "total": "121.00",
    "supplier": 'Ferretería "López"; -- S.L.',
    "tax_id": "",
    "invoice_number": "F-1",
    "concept": "Tornillos",
    "category": "Hogar",
}
EXPECTED = EntryInput(
    invoice_date=date(2025, 3, 1),
    entry_type="gasto",
    total=Decimal("121.00"),
    base_amount=Decimal("100.00"),
    vat_amount=Decimal("21.00"),
    supplier='Ferretería "López"; -- S.L.',
    invoice_number="F-1",
    concept="Tornillos",
    category="Hogar",
)
MISMATCH_FORM = {**VALID_FORM, "total": "130.00"}


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FailingCommitConnection:
    """Wraps a real connection; ``commit`` raises without committing."""

    def __init__(self, conn: sqlite3.Connection, *, rollback_fails: bool = False) -> None:
        self._conn = conn
        self._rollback_fails = rollback_fails

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def commit(self) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    def rollback(self) -> None:
        self._conn.rollback()  # really undo, then maybe fail as well
        if self._rollback_fails:
            raise sqlite3.OperationalError("rollback failed")


@pytest.fixture
def db(settings: Settings) -> Path:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    init_schema(settings.db_path)
    return settings.db_path


@pytest.fixture
def store(settings: Settings) -> ImageStore:
    return ImageStore(settings.images_dir)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(db: Path, store: ImageStore, clock: Clock) -> EntryService:
    return EntryService(store, lambda: connect(db), clock=clock, ttl=TTL)


def _make_draft(
    store: ImageStore,
    draft_id: str = DRAFT_A,
    data: bytes = JPEG,
    ext: str = "jpg",
    text: str = OCR_TEXT,
    modified: datetime = NOW - timedelta(hours=1),
) -> str:
    path = store.save_temp(draft_id, data, ext)
    store.save_temp_text(draft_id, text)
    ts = modified.timestamp()
    os.utime(path, (ts, ts))
    return draft_id


def _count_entries(db: Path) -> int:
    conn = connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    finally:
        conn.close()


def _final_images(store: ImageStore) -> list[str]:
    return sorted(p.name for p in store.root.iterdir() if p.is_file())


def _fields(entry) -> EntryInput:
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


def _saved_entry(service: EntryService, store: ImageStore, draft_id: str = DRAFT_A, **kw):
    _make_draft(store, draft_id, **kw)
    result = service.confirm(draft_id, VALID_FORM, mismatch_confirmed=False)
    assert result.saved
    return service.get(result.entry_id)


# -- confirm -------------------------------------------------------------------------------


@pytest.mark.parametrize(("data", "ext"), [(JPEG, "jpg"), (PNG, "png")])
def test_confirm_saves_entry_and_moves_image_byte_identical(service, store, db, data, ext):
    _make_draft(store, data=data, ext=ext)

    result = service.confirm(DRAFT_A, VALID_FORM, mismatch_confirmed=False)

    assert result.saved and result.errors == {}
    entry = service.get(result.entry_id)
    assert _fields(entry) == EXPECTED
    assert entry.ocr_text == OCR_TEXT
    assert entry.created_at == NOW
    assert entry.updated_at is None
    assert UUID_NAME.fullmatch(entry.image_filename)
    assert entry.image_filename.endswith("." + ext)
    assert store.resolve(entry.image_filename).read_bytes() == data
    assert _final_images(store) == [entry.image_filename]
    assert list(store.tmp.iterdir()) == []  # image moved, text discarded
    assert store.load_draft(DRAFT_A) is None
    assert _count_entries(db) == 1


def test_confirm_invalid_form_returns_errors_and_saves_nothing(service, store, db):
    _make_draft(store)
    form = {**VALID_FORM, "invoice_date": "2025-02-30", "total": ""}

    result = service.confirm(DRAFT_A, form, mismatch_confirmed=True)

    assert not result.saved
    assert set(result.errors) == {"invoice_date", "total"}
    assert result.form == form
    assert _count_entries(db) == 0
    assert _final_images(store) == []
    assert store.load_draft(DRAFT_A) is not None


def test_confirm_unconfirmed_mismatch_is_not_saved(service, store, db):
    _make_draft(store)

    result = service.confirm(DRAFT_A, MISMATCH_FORM, mismatch_confirmed=False)

    assert not result.saved
    assert result.errors == {}
    assert "totals" in result.warnings
    assert result.form == MISMATCH_FORM
    assert _count_entries(db) == 0
    assert store.load_draft(DRAFT_A) is not None


def test_confirm_confirmed_mismatch_is_saved_with_warning(service, store, db):
    _make_draft(store)

    result = service.confirm(DRAFT_A, MISMATCH_FORM, mismatch_confirmed=True)

    assert result.saved
    assert "totals" in result.warnings
    assert service.get(result.entry_id).total == Decimal("130.00")


@pytest.mark.parametrize("draft_id", [DRAFT_B, "../etc", "A" * 32, ""])
def test_confirm_missing_draft_raises_not_found(service, store, db, draft_id):
    _make_draft(store)  # another Borrador exists
    with pytest.raises(DraftNotFound):
        service.confirm(draft_id, VALID_FORM, mismatch_confirmed=False)
    assert _count_entries(db) == 0


def test_confirm_expired_draft_raises_not_found(service, store, db):
    _make_draft(store, modified=NOW - TTL - timedelta(seconds=1))
    with pytest.raises(DraftNotFound):
        service.confirm(DRAFT_A, VALID_FORM, mismatch_confirmed=False)
    assert _count_entries(db) == 0


def test_confirm_adds_new_category_to_suggestions(service, store):
    _make_draft(store)
    assert "Mascotas" not in service.category_names()

    service.confirm(DRAFT_A, {**VALID_FORM, "category": "Mascotas"}, mismatch_confirmed=False)
    _make_draft(store, DRAFT_B)
    service.confirm(DRAFT_B, {**VALID_FORM, "category": "mascotas"}, mismatch_confirmed=False)

    names = service.category_names()
    assert "Mascotas" in names
    assert [n.lower() for n in names].count("mascotas") == 1
    assert "Hogar" in names  # seed categories kept


def test_confirm_without_category_saves_none(service, store):
    _make_draft(store)
    result = service.confirm(DRAFT_A, {**VALID_FORM, "category": " "}, mismatch_confirmed=False)
    assert service.get(result.entry_id).category is None


# -- confirm: atomicity (Req. 6.5) ----------------------------------------------------------


def _fail_promote(monkeypatch, db):
    def boom(self, draft_id):
        raise OSError("disk full")

    monkeypatch.setattr(ImageStore, "promote", boom)
    return None


def _fail_insert(monkeypatch, db):
    def boom(self, *args, **kwargs):
        raise sqlite3.OperationalError("insert failed")

    monkeypatch.setattr(EntryRepository, "insert", boom)
    return None


def _fail_commit(monkeypatch, db):
    return lambda: FailingCommitConnection(connect(db))


def _fail_commit_and_rollback(monkeypatch, db):
    return lambda: FailingCommitConnection(connect(db), rollback_fails=True)


def _fail_connect(monkeypatch, db):
    def boom():
        raise sqlite3.OperationalError("unable to open database file")

    return boom


@pytest.mark.parametrize(
    "inject",
    [_fail_promote, _fail_insert, _fail_commit, _fail_commit_and_rollback, _fail_connect],
    ids=["promote", "insert", "commit", "commit+rollback", "connect"],
)
def test_confirm_failure_rolls_back_and_keeps_draft(
    inject, monkeypatch, store, db, clock, caplog
):
    previous = _saved_entry(EntryService(store, lambda: connect(db), clock=clock, ttl=TTL), store)
    images_before = _final_images(store)
    _make_draft(store, DRAFT_B, data=PNG, ext="png")
    connect_fn = inject(monkeypatch, db) or (lambda: connect(db))
    service = EntryService(store, connect_fn, clock=clock, ttl=TTL)
    form = {**VALID_FORM, "category": "Nueva categoría"}

    with caplog.at_level(logging.ERROR, logger="app.services"), pytest.raises(SaveError):
        service.confirm(DRAFT_B, form, mismatch_confirmed=False)

    assert "Error guardando el apunte" in caplog.text
    assert _count_entries(db) == 1
    assert _final_images(store) == images_before
    files = store.load_draft(DRAFT_B)
    assert files is not None  # the Borrador is still available (image back in tmp/)
    assert files.image_path.read_bytes() == PNG
    assert files.ocr_text == OCR_TEXT
    reader = EntryService(store, lambda: connect(db), clock=clock, ttl=TTL)
    assert "Nueva categoría" not in reader.category_names()
    assert reader.get(previous.id) == previous

    # Retry once the failure is gone.
    monkeypatch.undo()
    assert reader.confirm(DRAFT_B, form, mismatch_confirmed=False).saved


def test_confirm_failing_demote_still_raises_save_error(monkeypatch, service, store, db, caplog):
    _make_draft(store)
    _fail_insert(monkeypatch, db)

    def demote_boom(self, final_name, draft_id):
        raise OSError("cannot move back")

    monkeypatch.setattr(ImageStore, "demote", demote_boom)
    with caplog.at_level(logging.ERROR, logger="app.services"), pytest.raises(SaveError):
        service.confirm(DRAFT_A, VALID_FORM, mismatch_confirmed=False)
    assert "Error devolviendo la imagen" in caplog.text
    assert _count_entries(db) == 0


def test_confirm_failing_text_cleanup_keeps_saved_entry(monkeypatch, service, store, caplog):
    _make_draft(store)

    def boom(self, draft_id):
        raise PermissionError("locked")

    monkeypatch.setattr(ImageStore, "discard_draft_text", boom)
    with caplog.at_level(logging.ERROR, logger="app.services"):
        result = service.confirm(DRAFT_A, VALID_FORM, mismatch_confirmed=False)
    assert result.saved
    assert "texto temporal" in caplog.text


# -- update ---------------------------------------------------------------------------------


def test_update_valid_form_replaces_fields(service, store, clock):
    entry = _saved_entry(service, store)
    clock.now = LATER
    form = {**VALID_FORM, "entry_type": "ingreso", "concept": "Devolución", "category": "Regalos"}

    result = service.update(entry.id, form, mismatch_confirmed=False)

    assert result.saved and result.entry_id == entry.id
    updated = service.get(entry.id)
    assert updated.entry_type == "ingreso"
    assert updated.concept == "Devolución"
    assert updated.updated_at == LATER
    assert updated.created_at == entry.created_at
    assert updated.ocr_text == entry.ocr_text
    assert updated.image_filename == entry.image_filename
    assert "Regalos" in service.category_names()


def test_update_invalid_form_returns_errors_and_keeps_entry(service, store):
    entry = _saved_entry(service, store)
    form = {**VALID_FORM, "entry_type": "otro", "total": "-1"}

    result = service.update(entry.id, form, mismatch_confirmed=False)

    assert not result.saved
    assert set(result.errors) == {"entry_type", "total"}
    assert result.form == form
    assert service.get(entry.id) == entry


def test_update_unconfirmed_mismatch_is_rejected(service, store):
    entry = _saved_entry(service, store)
    result = service.update(entry.id, MISMATCH_FORM, mismatch_confirmed=False)
    assert not result.saved and "totals" in result.warnings
    assert service.update(entry.id, MISMATCH_FORM, mismatch_confirmed=True).saved


@pytest.mark.parametrize("form", [VALID_FORM, {"total": "x"}])
def test_update_missing_entry_raises_not_found(service, form):
    with pytest.raises(EntryNotFound):
        service.update(999, form, mismatch_confirmed=False)


def test_update_entry_deleted_concurrently_raises_not_found(monkeypatch, service, store):
    entry = _saved_entry(service, store)
    monkeypatch.setattr(EntryRepository, "update", lambda self, *a, **k: False)
    with pytest.raises(EntryNotFound):
        service.update(entry.id, VALID_FORM, mismatch_confirmed=False)


def test_update_failure_rolls_back_and_raises_save_error(monkeypatch, service, store, caplog):
    entry = _saved_entry(service, store)

    def boom(self, *args, **kwargs):
        raise sqlite3.OperationalError("update failed")

    monkeypatch.setattr(EntryRepository, "update", boom)
    form = {**VALID_FORM, "category": "Otra nueva"}
    with caplog.at_level(logging.ERROR, logger="app.services"), pytest.raises(SaveError):
        service.update(entry.id, form, mismatch_confirmed=False)
    assert "Error actualizando el apunte" in caplog.text
    assert service.get(entry.id) == entry
    assert "Otra nueva" not in service.category_names()


# -- delete ---------------------------------------------------------------------------------


def test_delete_removes_row_and_image(service, store, db):
    entry = _saved_entry(service, store)
    other = _saved_entry(service, store, DRAFT_B, data=PNG, ext="png")

    deleted = service.delete(entry.id)

    assert deleted == entry
    assert service.get(entry.id) is None
    assert store.resolve(entry.image_filename) is None
    assert service.get(other.id) == other
    assert store.resolve(other.image_filename).read_bytes() == PNG
    assert _count_entries(db) == 1


def test_delete_with_missing_image_logs_warning_and_succeeds(service, store, caplog):
    entry = _saved_entry(service, store)
    store.resolve(entry.image_filename).unlink()

    with caplog.at_level(logging.WARNING, logger="app.services"):
        service.delete(entry.id)

    assert service.get(entry.id) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and entry.image_filename in warnings[0].getMessage()


def test_delete_with_failing_image_removal_logs_orphan(monkeypatch, service, store, caplog):
    entry = _saved_entry(service, store)

    def boom(self, name):
        raise PermissionError("in use")

    monkeypatch.setattr(ImageStore, "delete", boom)
    with caplog.at_level(logging.ERROR, logger="app.services"):
        assert service.delete(entry.id) == entry
    assert service.get(entry.id) is None
    assert "No se ha podido borrar la imagen" in caplog.text


def test_delete_missing_entry_raises_not_found(service, store):
    entry = _saved_entry(service, store)
    with pytest.raises(EntryNotFound):
        service.delete(entry.id + 1)
    assert service.get(entry.id) == entry


def test_delete_database_failure_propagates_and_keeps_entry(monkeypatch, service, store):
    entry = _saved_entry(service, store)

    def boom(self, conn, entry_id):
        raise sqlite3.OperationalError("delete failed")

    monkeypatch.setattr(EntryRepository, "delete", boom)
    with pytest.raises(sqlite3.OperationalError):
        service.delete(entry.id)
    monkeypatch.undo()
    assert service.get(entry.id) == entry
    assert store.resolve(entry.image_filename) is not None


# -- read helpers and form helper -----------------------------------------------------------


def test_list_and_totals(service, store):
    expense = _saved_entry(service, store)
    _make_draft(store, DRAFT_B)
    income_id = service.confirm(
        DRAFT_B, {**VALID_FORM, "entry_type": "ingreso", "total": "50.00", "base_amount": "",
                  "vat_amount": ""}, mismatch_confirmed=False
    ).entry_id

    page = service.list(EntryFilter(), 1)
    assert page.total == 2 and {e.id for e in page.items} == {expense.id, income_id}
    assert [e.id for e in service.list(EntryFilter(entry_type="ingreso"), 1).items] == [income_id]
    totals = service.totals(EntryFilter())
    assert totals.expenses == Decimal("121.00") and totals.income == Decimal("50.00")


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        ({MISMATCH_FIELD: "on"}, True),
        ({MISMATCH_FIELD: "1"}, True),
        ({MISMATCH_FIELD: " TRUE "}, True),
        ({MISMATCH_FIELD: ""}, False),
        ({MISMATCH_FIELD: "off"}, False),
        ({}, False),
    ],
)
def test_is_mismatch_confirmed(form, expected):
    assert is_mismatch_confirmed(form) is expected
