"""Unit tests for the ``images`` and ``health`` Blueprints.

A minimal Flask app registers both Blueprints plus ``app.security`` and fills
``app.extensions["invoice"]`` with real services over ``tmp_path`` (``app.main`` is not used).
Responses that stream a file are always closed (``with client.get(...)``): on Windows an open
file cannot be deleted when ``tmp_path`` is cleaned up.

Requirements: 4.2, 8.5, 8.6, 13.1, 13.2, 13.3, 14.3, 14.4.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.exceptions import NotFound

from app import security
from app.config import Settings
from app.ocr import OcrStatus
from app.repository import connect, init_schema
from app.routes import health as health_routes
from app.routes import images as image_routes
from app.services import DraftService, EntryService
from app.storage import ImageStore
from app.uploads import JPEG_MAGIC, PNG_MAGIC
from tests.fakes import FakeOcrEngine

JPEG = JPEG_MAGIC + bytes(range(256)) + b"\r\n\x00\n\r"
PNG = PNG_MAGIC + b"\x00\xff\r\n" * 16
NOW = datetime(2025, 3, 2, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(hours=24)
OCR_OK = OcrStatus(available=True, languages=("spa", "eng"))
OCR_DOWN = OcrStatus(available=False, detail="Tesseract no encontrado")

VALID_FORM = {
    "invoice_date": "2025-03-01",
    "entry_type": "gasto",
    "base_amount": "100.00",
    "vat_amount": "21.00",
    "total": "121.00",
    "supplier": "Ferretería López",
    "tax_id": "",
    "invoice_number": "F-1",
    "concept": "Tornillos",
    "category": "Hogar",
}


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _build_app(settings: Settings, ocr_status: OcrStatus = OCR_OK) -> Flask:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    init_schema(settings.db_path)
    store = ImageStore(settings.images_dir)
    clock = Clock()
    app = Flask(__name__)
    app.extensions["invoice"] = {
        "settings": settings,
        "store": store,
        "clock": clock,
        "drafts": DraftService(store, FakeOcrEngine("Total 121,00"), clock=clock, ttl=TTL),
        "entries": EntryService(store, lambda: connect(settings.db_path), clock=clock, ttl=TTL),
        "ocr_status": ocr_status,
    }
    app.register_blueprint(image_routes.bp)
    app.register_blueprint(health_routes.bp)
    security.register(app)
    return app


@pytest.fixture
def app(settings: Settings) -> Flask:
    return _build_app(settings)


@pytest.fixture
def client(app: Flask):
    return app.test_client()


def _services(app: Flask) -> dict:
    return app.extensions["invoice"]


def _new_draft(app: Flask, data: bytes = JPEG, fmt: str = "jpg") -> str:
    """Create a Borrador through ``DraftService`` with an mtime one hour before ``NOW``."""
    view = _services(app)["drafts"].create(data, fmt)
    ts = (NOW - timedelta(hours=1)).timestamp()
    os.utime(_services(app)["store"].tmp / f"{view.draft_id}.{fmt}", (ts, ts))
    return view.draft_id


def _new_entry(app: Flask, data: bytes = JPEG, fmt: str = "jpg") -> int:
    draft_id = _new_draft(app, data, fmt)
    result = _services(app)["entries"].confirm(draft_id, VALID_FORM, mismatch_confirmed=False)
    assert result.entry_id is not None
    return result.entry_id


def _get(client, url: str):
    """GET ``url`` and return ``(status, headers, body)`` with the response already closed."""
    with client.get(url) as response:
        return response.status_code, response.headers, response.get_data()


# -- Apunte images ------------------------------------------------------------------------


FORMATS = pytest.mark.parametrize(
    ("data", "fmt", "mimetype"),
    [(JPEG, "jpg", "image/jpeg"), (PNG, "png", "image/png")],
    ids=["jpg", "png"],
)


@FORMATS
def test_entry_image_is_served_byte_for_byte(app, client, data, fmt, mimetype):
    entry_id = _new_entry(app, data, fmt)

    status, headers, body = _get(client, f"/entries/{entry_id}/image")

    assert status == 200
    assert body == data
    assert headers["Content-Type"] == mimetype
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Disposition" not in headers  # no file name or path leaks
    assert "private" in headers["Cache-Control"]
    assert "no-cache" in headers["Cache-Control"]


def test_entry_image_supports_conditional_requests(app, client):
    entry_id = _new_entry(app)
    _, headers, _ = _get(client, f"/entries/{entry_id}/image")

    with client.get(f"/entries/{entry_id}/image", headers={"If-None-Match": headers["ETag"]}) as resp:
        assert resp.status_code == 304


def test_entry_image_unknown_entry_is_404(client):
    status, headers, _ = _get(client, "/entries/999/image")
    assert status == 404
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_entry_image_missing_file_is_404(app, client):
    entry_id = _new_entry(app)
    entry = _services(app)["entries"].get(entry_id)
    (_services(app)["store"].root / entry.image_filename).unlink()

    assert _get(client, f"/entries/{entry_id}/image")[0] == 404


def test_entry_image_with_bad_stored_name_is_404(app, client, settings):
    """A stored name that is not a generated UUID name is never resolved (Req. 14.3)."""
    entry_id = _new_entry(app)
    outside = settings.images_dir.parent / "secret.jpg"
    outside.write_bytes(JPEG)
    conn = connect(settings.db_path)
    try:
        conn.execute("UPDATE entries SET image_filename = ? WHERE id = ?", ("../secret.jpg", entry_id))
    finally:
        conn.close()

    assert _get(client, f"/entries/{entry_id}/image")[0] == 404


@pytest.mark.parametrize(
    "url",
    [
        "/entries/abc/image",
        "/entries/-1/image",
        "/entries/1.5/image",
        "/entries/..%2F..%2Fx/image",
        "/entries/99999999999999999999999/image",
    ],
)
def test_entry_image_invalid_ids_are_404(client, url):
    assert _get(client, url)[0] == 404


# -- Borrador images ----------------------------------------------------------------------


@FORMATS
def test_draft_image_is_served(app, client, data, fmt, mimetype):
    draft_id = _new_draft(app, data, fmt)

    status, headers, body = _get(client, f"/drafts/{draft_id}/image")

    assert status == 200
    assert body == data
    assert headers["Content-Type"] == mimetype
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Disposition" not in headers


def test_draft_image_unknown_draft_is_404(client):
    assert _get(client, f"/drafts/{'f' * 32}/image")[0] == 404


def test_draft_image_expired_draft_is_404(app, client):
    draft_id = _new_draft(app)
    _services(app)["clock"].now = NOW + TTL  # image mtime is NOW - 1 h → older than the TTL

    assert _get(client, f"/drafts/{draft_id}/image")[0] == 404


def test_draft_image_after_cancel_is_404(app, client):
    draft_id = _new_draft(app)
    _services(app)["drafts"].cancel(draft_id)

    assert _get(client, f"/drafts/{draft_id}/image")[0] == 404


@pytest.mark.parametrize(
    "url",
    [
        "/drafts/..%2F..%2Fx/image",
        "/drafts/../../x/image",
        "/drafts/%2E%2E/image",
        "/drafts/" + "A" * 32 + "/image",
        "/drafts/" + "a" * 31 + "/image",
        "/drafts/" + "a" * 33 + "/image",
        "/drafts/" + "a" * 32 + "%0A/image",
        "/drafts/" + "a" * 32 + ".jpg/image",
    ],
)
def test_draft_image_malformed_ids_are_404(app, client, url):
    _new_draft(app)  # the store is not empty, yet nothing is reachable
    assert _get(client, url)[0] == 404


def test_draft_image_outside_tmp_is_404(app, client, settings, monkeypatch):
    """Defence in depth: a Borrador path that escapes ``tmp/`` is never served (Req. 14.4)."""
    draft_id = _new_draft(app)
    files = _services(app)["drafts"].load_files(draft_id)
    outside = settings.images_dir.parent / "secret.jpg"
    outside.write_bytes(JPEG)
    monkeypatch.setattr(
        _services(app)["drafts"], "load_files", lambda _id: replace(files, image_path=outside)
    )

    assert _get(client, f"/drafts/{draft_id}/image")[0] == 404


def test_mimetype_only_for_jpg_and_png(app):
    assert image_routes._mimetype(Path("a.JPG")) == "image/jpeg"
    assert image_routes._mimetype(Path("a.png")) == "image/png"
    with app.test_request_context(), pytest.raises(NotFound):
        image_routes._mimetype(Path("a.gif"))


def test_inside_rejects_invalid_paths(tmp_path):
    assert image_routes._inside(tmp_path / "missing.jpg", tmp_path) is None
    assert image_routes._inside(Path("bad\0name.jpg"), tmp_path) is None


def test_draft_route_never_serves_final_images(app, client):
    entry_id = _new_entry(app)
    name = _services(app)["entries"].get(entry_id).image_filename
    draft_like = name.split(".")[0]  # a valid DRAFT_ID that only exists in root/, not tmp/

    assert _get(client, f"/drafts/{draft_like}/image")[0] == 404


# -- /health ------------------------------------------------------------------------------


def test_health_ok(client, app):
    start = time.monotonic()
    response = client.get("/health")

    assert time.monotonic() - start < 2
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.get_json() == {
        "status": "ok",
        "database": {"ok": True},
        "images": {"ok": True, "writable": True},
        "ocr": {"ok": True, "languages": ["spa", "eng"]},
    }
    assert list(_services(app)["store"].tmp.iterdir()) == []


def test_health_degraded_when_ocr_unavailable(settings):
    client = _build_app(settings, OCR_DOWN).test_client()

    response = client.get("/health")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "degraded"
    assert body["ocr"] == {"ok": False, "languages": []}
    assert body["database"] == {"ok": True}


def test_health_503_when_database_missing(app, client, settings):
    settings.db_path.unlink()

    response = client.get("/health")

    assert response.status_code == 503
    body = response.get_json()
    assert body["status"] == "error"
    assert body["database"] == {"ok": False}
    assert body["images"] == {"ok": True, "writable": True}
    assert set(body) == {"status", "database", "images", "ocr"}
    assert not settings.db_path.exists()


def test_health_503_when_image_store_not_writable(app, client):
    tmp = _services(app)["store"].tmp
    tmp.rmdir()

    response = client.get("/health")

    assert response.status_code == 503
    assert response.get_json()["images"] == {"ok": False, "writable": False}
