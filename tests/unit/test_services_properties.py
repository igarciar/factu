"""Pruebas de propiedades de ``EntryService`` (Properties 14, 15, 16, 17 y 21).

Cada ejemplo usa su propio ``tempfile.TemporaryDirectory()`` con una Base_Datos y un
Almacén_Imágenes nuevos. Los fallos se inyectan construyendo el servicio con colaboradores
que fallan (subclases de ``ImageStore``/``EntryRepository`` o un envoltorio de conexión), sin
``monkeypatch``, para no mezclar fixtures de función con Hypothesis.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.3, 9.3, 14.5, 14.6.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.models import EntryInput
from app.repository import EntryRepository, connect, init_schema
from app.services import EntryService, SaveError
from app.storage import UUID_NAME, ImageStore
from tests.strategies import (
    JPEG_SIGNATURE,
    PNG_SIGNATURE,
    amounts,
    dates,
    image_bytes,
    valid_tax_ids,
)

NOW = datetime(2025, 3, 2, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(hours=24)

BASE_FORM = {
    "invoice_date": "2025-03-01",
    "entry_type": "gasto",
    "base_amount": "100.00",
    "vat_amount": "21.00",
    "total": "121.00",
    "supplier": "Proveedor",
    "tax_id": "",
    "invoice_number": "F-1",
    "concept": "Concepto",
    "category": "Hogar",
}

# Texto Unicode arbitrario (sin sustitutos sueltos, que no se pueden codificar en UTF-8).
_CHARS = st.characters(blacklist_categories=("Cs",))
_TRICKY = st.sampled_from(
    ["'", '"', ";", "--", "' OR '1'='1", "'); DROP TABLE entries; --", "\\", "%", "_",
     "ñ", "€", "Ω", "😀", "\u200b", "\t", "\n"]
)


def _tricky_text(max_len: int) -> st.SearchStrategy[str]:
    """Cadenas con comillas, ``;``, ``--`` y Unicode, de hasta ``max_len`` caracteres."""
    pieces = st.one_of(_TRICKY, st.text(_CHARS, max_size=6))
    return st.lists(pieces, max_size=6).map(lambda parts: "".join(parts)[:max_len])


def _amount_text(amount) -> str:
    return "" if amount is None else f"{amount:.2f}"


@st.composite
def entry_forms(draw: st.DrawFn) -> tuple[dict[str, str], EntryInput]:
    """Formulario válido y el ``EntryInput`` que debe resultar de él."""
    invoice_date = draw(dates())
    entry_type = draw(st.sampled_from(["gasto", "ingreso"]))
    total = draw(amounts())
    base = draw(st.one_of(st.none(), amounts()))
    vat = draw(st.one_of(st.none(), amounts()))
    tax_id = draw(st.one_of(st.none(), valid_tax_ids()))
    texts = {
        "supplier": draw(_tricky_text(200)),
        "invoice_number": draw(_tricky_text(40)),
        "concept": draw(_tricky_text(200)),
        "category": draw(_tricky_text(60)),
    }
    form = {
        "invoice_date": invoice_date.isoformat(),
        "entry_type": entry_type,
        "base_amount": _amount_text(base),
        "vat_amount": _amount_text(vat),
        "total": _amount_text(total),
        "tax_id": tax_id or "",
        **texts,
    }
    expected = EntryInput(
        invoice_date=invoice_date,
        entry_type=entry_type,
        total=total,
        base_amount=base,
        vat_amount=vat,
        tax_id=tax_id,
        **{name: value.strip() or None for name, value in texts.items()},
    )
    return form, expected


draft_ids = st.uuids().map(lambda u: u.hex)


# --------------------------------------------------------------------------- #
# Entorno y colaboradores
# --------------------------------------------------------------------------- #


@contextmanager
def _environment() -> Iterator[tuple[Path, ImageStore]]:
    """Base_Datos inicializada y Almacén_Imágenes vacío en un directorio temporal propio."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "db" / "invoices.db"
        db.parent.mkdir()
        init_schema(db)
        yield db, ImageStore(root / "images")


def _service(db: Path, store: ImageStore, **kwargs) -> EntryService:
    kwargs.setdefault("connect", lambda: connect(db))
    return EntryService(store, clock=lambda: NOW, ttl=TTL, **kwargs)


def _make_draft(store: ImageStore, draft_id: str, ext: str, data: bytes, text: str = "OCR") -> None:
    path = store.save_temp(draft_id, data, ext)
    store.save_temp_text(draft_id, text)
    ts = (NOW - timedelta(hours=1)).timestamp()
    os.utime(path, (ts, ts))


def _final_files(store: ImageStore) -> dict[str, bytes]:
    """Ficheros del Almacén_Imágenes sin ``tmp/``."""
    return {p.name: p.read_bytes() for p in store.root.iterdir() if p.is_file()}


def _db_rows(db: Path) -> tuple[list[tuple], list[tuple]]:
    conn = connect(db)
    try:
        entries = [tuple(r) for r in conn.execute("SELECT * FROM entries ORDER BY id")]
        categories = [tuple(r) for r in conn.execute("SELECT name FROM categories ORDER BY name")]
    finally:
        conn.close()
    return entries, categories


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


def _signature_ext(data: bytes) -> str:
    if data.startswith(JPEG_SIGNATURE):
        return "jpg"
    assert data.startswith(PNG_SIGNATURE)
    return "png"


class FailingPromoteStore(ImageStore):
    """``promote`` falla sin mover nada (como un ``os.replace`` que no puede completarse)."""

    def promote(self, draft_id: str) -> str:
        raise OSError("disk full")


class FailingInsertRepository(EntryRepository):
    """``insert`` falla antes de escribir o después de haber insertado la fila."""

    def __init__(self, after_write: bool) -> None:
        self.after_write = after_write

    def insert(self, conn, *args, **kwargs):
        if self.after_write:
            super().insert(conn, *args, **kwargs)
        raise sqlite3.OperationalError("insert failed")


class FailingCommitConnection:
    """Envuelve una conexión real; ``commit`` lanza sin confirmar nada."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def commit(self) -> None:
        raise sqlite3.OperationalError("disk I/O error")


FAILURE_POINTS = ("promote", "insert_before_write", "insert_after_write", "commit")


def _failing_service(point: str, db: Path, store: ImageStore) -> EntryService:
    if point == "promote":
        return _service(db, FailingPromoteStore(store.root))
    if point.startswith("insert"):
        repo = FailingInsertRepository(after_write=point == "insert_after_write")
        return _service(db, store, entries=repo)
    assert point == "commit"
    return _service(db, store, connect=lambda: FailingCommitConnection(connect(db)))


# --------------------------------------------------------------------------- #
# Property 14
# --------------------------------------------------------------------------- #


# Feature: invoice-reader, Property 14: Round-trip de guardado de Apunte e imagen
@settings(max_examples=100, deadline=None)
@given(
    case=entry_forms(),
    image=image_bytes(max_tail=128),
    ocr_text=st.text(_CHARS, max_size=120),
    draft_id=draft_ids,
)
def test_confirm_round_trips_fields_ocr_text_and_image(case, image, ocr_text, draft_id):
    """**Validates: Requirements 6.1, 6.3, 14.6**"""
    form, expected = case
    ext, data = image
    with _environment() as (db, store):
        service = _service(db, store)
        _make_draft(store, draft_id, ext, data, ocr_text)

        result = service.confirm(draft_id, form, mismatch_confirmed=True)

        assert result.saved, result.errors
        entry = service.get(result.entry_id)
        assert entry is not None
        assert _fields(entry) == expected
        assert entry.ocr_text == ocr_text
        assert entry.created_at is not None
        path = store.resolve(entry.image_filename)
        assert path is not None and path.read_bytes() == data


# --------------------------------------------------------------------------- #
# Property 15
# --------------------------------------------------------------------------- #

client_filenames = st.one_of(
    st.sampled_from(
        ["../../etc/passwd", "..\\..\\boot.ini", "/etc/passwd", "C:\\Windows\\factura.jpg",
         "factura.jpg", "factura.png", "a/../../b.png", "nul.jpg\x00.png", ""]
    ),
    st.text(_CHARS, max_size=30),
)


# Feature: invoice-reader, Property 15: Nombre de imagen generado y único
@settings(max_examples=100, deadline=None)
@given(
    uploads=st.lists(
        st.tuples(draft_ids, image_bytes(max_tail=32), client_filenames),
        min_size=1,
        max_size=5,
        unique_by=lambda upload: upload[0],
    )
)
def test_confirmed_images_get_generated_unique_names(uploads):
    """**Validates: Requirements 6.2, 6.4, 14.5**"""
    with _environment() as (db, store):
        service = _service(db, store)
        entries = []
        for draft_id, (ext, data), client_name in uploads:
            _make_draft(store, draft_id, ext, data)
            # The client file name travels with the form under every plausible key.
            form = {**BASE_FORM, "filename": client_name, "image_filename": client_name,
                    "image": client_name}
            result = service.confirm(draft_id, form, mismatch_confirmed=False)
            assert result.saved, result.errors
            entry = service.get(result.entry_id)
            assert UUID_NAME.fullmatch(entry.image_filename)
            assert entry.image_filename.rsplit(".", 1)[1] == _signature_ext(data)
            assert not entry.image_filename.startswith(draft_id)
            entries.append(entry)

        names = [entry.image_filename for entry in entries]
        assert len(set(names)) == len(names)
        entry_rows, _ = _db_rows(db)
        files = _final_files(store)
        assert len(files) == len(entry_rows) == len(uploads)
        assert set(files) == set(names)


# --------------------------------------------------------------------------- #
# Property 16
# --------------------------------------------------------------------------- #


# Feature: invoice-reader, Property 16: Atomicidad ante fallos
@settings(max_examples=100, deadline=None)
@given(
    previous=st.lists(
        st.tuples(draft_ids, image_bytes(max_tail=32)), max_size=3, unique_by=lambda p: p[0]
    ),
    draft_id=draft_ids,
    image=image_bytes(max_tail=32),
    point=st.sampled_from(FAILURE_POINTS),
    category=_tricky_text(60).filter(lambda s: s.strip()),
)
def test_failed_confirm_leaves_database_and_store_unchanged(
    previous, draft_id, image, point, category
):
    """**Validates: Requirements 6.5**"""
    assume(draft_id not in {p[0] for p in previous})
    ext, data = image
    with _environment() as (db, store):
        working = _service(db, store)
        for prev_id, (prev_ext, prev_data) in previous:
            _make_draft(store, prev_id, prev_ext, prev_data)
            assert working.confirm(prev_id, BASE_FORM, mismatch_confirmed=False).saved
        _make_draft(store, draft_id, ext, data, "Texto del borrador")
        rows_before = _db_rows(db)
        files_before = _final_files(store)
        form = {**BASE_FORM, "category": category}

        with pytest.raises(SaveError):
            _failing_service(point, db, store).confirm(draft_id, form, mismatch_confirmed=False)

        assert _db_rows(db) == rows_before
        assert _final_files(store) == files_before
        draft = store.load_draft(draft_id)
        assert draft is not None
        assert draft.image_path.read_bytes() == data
        assert draft.ocr_text == "Texto del borrador"
        # The Borrador can still be confirmed once the failure is gone.
        assert working.confirm(draft_id, form, mismatch_confirmed=False).saved


# --------------------------------------------------------------------------- #
# Property 17
# --------------------------------------------------------------------------- #

_CASE_CHANGES = st.sampled_from([str.upper, str.lower, str.swapcase, str.title, str.capitalize])


def _fold_counts(names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in names:
        counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
    return counts


# Feature: invoice-reader, Property 17: Las categorías nuevas se incorporan a las sugerencias
@settings(max_examples=100, deadline=None)
@given(
    raw=st.text(_CHARS, min_size=1, max_size=60).filter(lambda s: s.strip()),
    change_case=_CASE_CHANGES,
    ids=st.lists(draft_ids, min_size=2, max_size=2, unique=True),
)
def test_new_categories_join_suggestions_without_case_duplicates(raw, change_case, ids):
    """**Validates: Requirements 7.3**"""
    variant = change_case(raw)
    assume(variant.strip() and len(variant.strip()) <= 60)
    name = raw.strip()
    with _environment() as (db, store):
        service = _service(db, store)
        before = service.category_names()
        known = any(existing.casefold() == name.casefold() for existing in before)

        for draft_id, category in zip(ids, (raw, variant)):
            _make_draft(store, draft_id, "jpg", JPEG_SIGNATURE)
            form = {**BASE_FORM, "category": category}
            assert service.confirm(draft_id, form, mismatch_confirmed=False).saved

        after = service.category_names()
        if not known:
            assert name in after
        assert _fold_counts(after)[name.casefold()] == 1
        assert all(count == 1 for count in _fold_counts(after).values())
        assert set(before) <= set(after)


# --------------------------------------------------------------------------- #
# Property 21
# --------------------------------------------------------------------------- #


# Feature: invoice-reader, Property 21: La eliminación borra Apunte e imagen
@settings(max_examples=100, deadline=None)
@given(
    uploads=st.lists(
        st.tuples(draft_ids, image_bytes(max_tail=32)),
        min_size=1,
        max_size=4,
        unique_by=lambda upload: upload[0],
    ),
    pick=st.integers(min_value=0, max_value=3),
)
def test_delete_removes_entry_and_its_image_only(uploads, pick):
    """**Validates: Requirements 9.3**"""
    with _environment() as (db, store):
        service = _service(db, store)
        saved = []
        for draft_id, (ext, data) in uploads:
            _make_draft(store, draft_id, ext, data)
            result = service.confirm(draft_id, BASE_FORM, mismatch_confirmed=False)
            saved.append((service.get(result.entry_id), data))
        chosen, _ = saved[pick % len(saved)]
        others = [(entry, data) for entry, data in saved if entry.id != chosen.id]

        assert service.delete(chosen.id) == chosen

        assert service.get(chosen.id) is None
        assert store.resolve(chosen.image_filename) is None
        assert not (store.root / chosen.image_filename).exists()
        for entry, data in others:
            assert service.get(entry.id) == entry
            path = store.resolve(entry.image_filename)
            assert path is not None and path.read_bytes() == data
        assert set(_final_files(store)) == {entry.image_filename for entry, _ in others}
        assert len(_db_rows(db)[0]) == len(others)
