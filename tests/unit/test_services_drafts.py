"""Unit tests for DraftService and DraftPurger (Req. 2.3, 2.4, 2.5, 4.1, 4.4, 4.5, 4.6)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import extractor
from app.extractor import FIELDS
from app.models import DraftView
from app.ocr import OcrError, OcrTimeout, OcrUnavailable
from app.services import DraftPurger, DraftService, empty_draft, is_draft_expired, utc_now
from app.storage import DRAFT_ID, ImageStore
from app.uploads import JPEG_MAGIC, PNG_MAGIC
from tests.fakes import FakeOcrEngine

JPEG = JPEG_MAGIC + bytes(range(256)) + b"\r\n\x00\n\r"
PNG = PNG_MAGIC + b"\x00\xff\r\n" * 16
NOW = datetime(2025, 3, 2, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(hours=24)
INVOICE_TEXT = (
    "Ferretería López S.L.\n"
    "CIF B-12345674\n"
    "Factura nº F-2025-001\n"
    "Fecha: 01/03/2025\n"
    "Base imponible 100,00 €\n"
    "IVA 21,00 €\n"
    "Total 121,00 €\n"
)
EMPTY_MISSING = frozenset(FIELDS) - {"entry_type"}


@pytest.fixture
def store(tmp_path: Path) -> ImageStore:
    return ImageStore(tmp_path / "images")


def _service(store: ImageStore, ocr: FakeOcrEngine, now: datetime = NOW) -> DraftService:
    return DraftService(store, ocr, clock=lambda: now, ttl=TTL)


def _set_mtime(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def _assert_empty(view: DraftView, notice: str | None) -> None:
    assert set(view.draft.values) == set(FIELDS)
    assert view.draft.values["entry_type"] == "gasto"
    assert all(v == "" for k, v in view.draft.values.items() if k != "entry_type")
    assert view.draft.missing == EMPTY_MISSING
    assert view.draft.notice == notice


# -- empty_draft / helpers ---------------------------------------------------------------


def test_empty_draft_has_all_fields_blank_except_entry_type():
    draft = empty_draft("no_text")
    assert set(draft.values) == set(FIELDS)
    assert draft.values["entry_type"] == "gasto"
    assert draft.missing == EMPTY_MISSING
    assert draft.notice == "no_text"
    assert empty_draft().notice is None


def test_utc_now_is_aware_utc():
    assert utc_now().utcoffset() == timedelta(0)


# -- DraftService.create -----------------------------------------------------------------


@pytest.mark.parametrize("exc", [OcrTimeout("lento"), OcrUnavailable("sin spa"), OcrError("imagen corrupta")])
def test_create_ocr_failure_gives_empty_draft_and_logs_error(store, caplog, exc):
    ocr = FakeOcrEngine(raises=exc)
    with caplog.at_level(logging.ERROR, logger="app.services"):
        view = _service(store, ocr).create(JPEG, "jpg")
    _assert_empty(view, "ocr_failed")
    assert view.ocr_text == ""
    assert any(r.levelno == logging.ERROR and r.name == "app.services" for r in caplog.records)
    # The image is kept so the Usuario can still fill the form by hand.
    files = store.load_draft(view.draft_id)
    assert files is not None and files.image_path.read_bytes() == JPEG
    assert files.ocr_text == ""


@pytest.mark.parametrize("text", ["", "   ", "\n\t \r\n"])
def test_create_blank_text_gives_no_text_notice(store, text):
    view = _service(store, FakeOcrEngine(text=text)).create(PNG, "png")
    _assert_empty(view, "no_text")
    assert view.ocr_text == text
    assert store.load_draft(view.draft_id).ocr_text == text


def test_create_extracts_fields_and_stores_temp_files(store):
    ocr = FakeOcrEngine(text=INVOICE_TEXT)
    view = _service(store, ocr).create(JPEG, "jpg")

    assert DRAFT_ID.fullmatch(view.draft_id)
    assert view.draft == extractor.extract(INVOICE_TEXT)
    assert view.draft.notice is None
    assert view.draft.values["total"] == "121.00"
    assert view.ocr_text == INVOICE_TEXT
    assert ocr.calls == [JPEG]

    files = store.load_draft(view.draft_id)
    assert files is not None
    assert files.ext == "jpg"
    assert files.image_path == store.tmp / f"{view.draft_id}.jpg"
    assert files.image_path.read_bytes() == JPEG  # bytes unchanged (Req. 6.3)
    assert files.ocr_text == INVOICE_TEXT


def test_create_generates_a_new_draft_id_each_time(store):
    service = _service(store, FakeOcrEngine(text=INVOICE_TEXT))
    ids = {service.create(PNG, "png").draft_id for _ in range(3)}
    assert len(ids) == 3
    assert sorted(p.name for p in store.tmp.iterdir()) == sorted(
        [f"{i}.png" for i in ids] + [f"{i}.txt" for i in ids]
    )


def test_create_does_not_touch_final_store(store):
    _service(store, FakeOcrEngine(text=INVOICE_TEXT)).create(JPEG, "jpg")
    assert [p.name for p in store.root.iterdir()] == ["tmp"]  # nothing saved as Apunte (4.4)


# -- DraftService.get / load_files -------------------------------------------------------


def test_get_rebuilds_draft_from_stored_text(store):
    service = _service(store, FakeOcrEngine(text=INVOICE_TEXT))
    created = service.create(JPEG, "jpg")
    assert service.get(created.draft_id) == created


def test_get_blank_text_returns_empty_draft_without_notice(store):
    service = _service(store, FakeOcrEngine(raises=OcrTimeout("x")))
    created = service.create(JPEG, "jpg")
    view = service.get(created.draft_id)
    assert view is not None
    _assert_empty(view, None)


@pytest.mark.parametrize("draft_id", ["0" * 32, "../etc/passwd", "ABC", ""])
def test_get_unknown_or_malformed_id_returns_none(store, draft_id):
    assert _service(store, FakeOcrEngine(text="x")).get(draft_id) is None


def test_get_expired_draft_returns_none(store):
    created = _service(store, FakeOcrEngine(text=INVOICE_TEXT)).create(JPEG, "jpg")
    image = store.tmp / f"{created.draft_id}.jpg"
    _set_mtime(image, NOW - TTL - timedelta(seconds=1))
    assert _service(store, FakeOcrEngine(), now=NOW).get(created.draft_id) is None
    _set_mtime(image, NOW - TTL + timedelta(seconds=1))
    assert _service(store, FakeOcrEngine(), now=NOW).get(created.draft_id) is not None


def test_is_draft_expired_boundary(store):
    created = _service(store, FakeOcrEngine(text="x")).create(PNG, "png")
    _set_mtime(store.tmp / f"{created.draft_id}.png", NOW - TTL)
    files = store.load_draft(created.draft_id)
    assert not is_draft_expired(files, NOW, TTL)  # exactly ttl old: kept, as in the purge
    assert is_draft_expired(files, NOW + timedelta(seconds=1), TTL)


# -- DraftService.cancel -----------------------------------------------------------------


def test_cancel_removes_temp_files(store):
    service = _service(store, FakeOcrEngine(text=INVOICE_TEXT))
    keep = service.create(PNG, "png")
    gone = service.create(JPEG, "jpg")

    service.cancel(gone.draft_id)

    assert store.load_draft(gone.draft_id) is None
    assert not (store.tmp / f"{gone.draft_id}.jpg").exists()
    assert not (store.tmp / f"{gone.draft_id}.txt").exists()
    assert store.load_draft(keep.draft_id) is not None


def test_cancel_unknown_or_malformed_id_is_noop(store):
    service = _service(store, FakeOcrEngine())
    service.cancel("0" * 32)
    service.cancel("../x")


# -- DraftPurger -------------------------------------------------------------------------


class FakeMonotonic:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RecordingStore:
    """Stand-in for ImageStore that records purge calls and can fail on demand."""

    def __init__(self, result: list[str] | None = None, raises: BaseException | None = None) -> None:
        self.calls: list[tuple[datetime, timedelta]] = []
        self.result = result or []
        self.raises = raises

    def purge_expired_drafts(self, now: datetime, ttl: timedelta) -> list[str]:
        self.calls.append((now, ttl))
        if self.raises is not None:
            raise self.raises
        return list(self.result)


def _purger(store, mono: FakeMonotonic, interval: timedelta = timedelta(hours=1)) -> DraftPurger:
    return DraftPurger(store, ttl=TTL, interval=interval, clock=lambda: NOW, monotonic=mono)


def test_purger_defaults():
    purger = DraftPurger(RecordingStore())
    assert purger.ttl == timedelta(hours=24)
    assert purger.interval == timedelta(hours=1)


def test_purge_now_calls_store_with_clock_time_and_ttl():
    store = RecordingStore(result=["a.jpg"])
    assert _purger(store, FakeMonotonic()).purge_now() == ["a.jpg"]
    assert store.calls == [(NOW, TTL)]


def test_purge_now_propagates_errors():
    purger = _purger(RecordingStore(raises=OSError("disco")), FakeMonotonic())
    with pytest.raises(OSError):
        purger.purge_now()


def test_maybe_purge_first_call_purges():
    store = RecordingStore(result=["a.txt"])
    assert _purger(store, FakeMonotonic()).maybe_purge() == ["a.txt"]
    assert len(store.calls) == 1


def test_maybe_purge_respects_interval():
    store = RecordingStore(result=["a.jpg"])
    mono = FakeMonotonic(1000.0)
    purger = _purger(store, mono)

    assert purger.maybe_purge() == ["a.jpg"]
    mono.value += 3599.0
    assert purger.maybe_purge() == []  # before interval: no purge
    assert len(store.calls) == 1
    mono.value += 1.0  # exactly one hour after the last purge
    assert purger.maybe_purge() == ["a.jpg"]
    assert len(store.calls) == 2
    mono.value += 10.0
    assert purger.maybe_purge() == []
    assert len(store.calls) == 2


def test_purge_now_resets_interval_mark():
    store = RecordingStore()
    mono = FakeMonotonic()
    purger = _purger(store, mono)
    purger.purge_now()  # e.g. the startup purge
    mono.value += 60
    assert purger.maybe_purge() == []
    assert len(store.calls) == 1


@pytest.mark.parametrize("exc", [FileNotFoundError("tmp"), OSError("permiso"), RuntimeError("inesperado")])
def test_maybe_purge_logs_and_swallows_errors(caplog, exc):
    store = RecordingStore(raises=exc)
    mono = FakeMonotonic()
    purger = _purger(store, mono)
    with caplog.at_level(logging.ERROR, logger="app.services"):
        assert purger.maybe_purge() == []
    records = [r for r in caplog.records if r.name == "app.services"]
    assert records and records[0].exc_info is not None  # logger.exception
    # A failed purge still counts: no retry on every request.
    mono.value += 1
    assert purger.maybe_purge() == []
    assert len(store.calls) == 1


def test_maybe_purge_deletes_only_expired_real_files(store):
    service = _service(store, FakeOcrEngine(text=INVOICE_TEXT))
    old = service.create(JPEG, "jpg")
    fresh = service.create(PNG, "png")
    for name in (f"{old.draft_id}.jpg", f"{old.draft_id}.txt"):
        _set_mtime(store.tmp / name, NOW - TTL - timedelta(minutes=1))
    for name in (f"{fresh.draft_id}.png", f"{fresh.draft_id}.txt"):
        _set_mtime(store.tmp / name, NOW - timedelta(minutes=1))

    removed = _purger(store, FakeMonotonic()).maybe_purge()

    assert sorted(removed) == sorted([f"{old.draft_id}.jpg", f"{old.draft_id}.txt"])
    assert store.load_draft(old.draft_id) is None
    assert store.load_draft(fresh.draft_id) is not None
