"""Unit tests of the ``pages`` blueprint: upload, review, confirm and cancel (task 12.3).

The Flask app is built here (``app.main`` is task 12.6) with the real services over
``tmp_path`` and ``FakeOcrEngine``, plus a 404 handler equivalent to the one of 12.6.

Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 4.1, 4.2, 4.3, 4.4, 4.5, 5.5, 5.7, 6.6, 7.2, 12.4.
"""

from __future__ import annotations

import dataclasses
import io
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import Flask, render_template

import app as app_package
from app import security
from app.config import Settings
from app.ocr import OcrTimeout
from app.repository import EntryRepository, connect, init_schema
from app.routes import pages
from app.services import DashboardService, DraftPurger, DraftService, EntryService
from app.storage import ImageStore
from app.uploads import JPEG_MAGIC, PNG_MAGIC
from tests.fakes import FakeOcrEngine

APP_DIR = Path(app_package.__file__).parent
JPEG = JPEG_MAGIC + b"\x00\x10JFIF" + bytes(range(64))
PNG = PNG_MAGIC + b"\x00" * 32
OCR_TEXT = (
    "FERRETERIA LOPEZ\n"
    "Factura nº F-2025-001\n"
    "Fecha: 01/03/2025\n"
    "Base imponible 100,00 €\n"
    "IVA 21% 21,00 €\n"
    "Total 121,00 €\n"
)
VALID_FORM = {
    "invoice_date": "2025-03-01",
    "entry_type": "gasto",
    "supplier": "FERRETERIA LOPEZ",
    "tax_id": "",
    "invoice_number": "F-2025-001",
    "concept": "Tornillos",
    "category": "Hogar",
    "base_amount": "100.00",
    "vat_amount": "21.00",
    "total": "121.00",
}
UNKNOWN_DRAFT = "f" * 32
CONFIRM_ACTION = re.compile(r'action="/drafts/([0-9a-f]{32})/confirm"')


# --------------------------------------------------------------------------- helpers


class _Element:
    def __init__(self, tag: str, attrs: dict[str, str | None], ancestors: list["_Element"]):
        self.tag = tag
        self.attrs = attrs
        self.ancestors = ancestors

    def ancestor_classes(self) -> set[str]:
        classes: set[str] = set()
        for element in self.ancestors:
            classes.update((element.attrs.get("class") or "").split())
        return classes


class _Collector(HTMLParser):
    """Collects every start tag with its attributes and its open ancestors."""

    VOID = {"input", "img", "meta", "link", "br", "hr", "source"}

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[_Element] = []
        self._stack: list[_Element] = []

    def handle_starttag(self, tag, attrs):
        element = _Element(tag, dict(attrs), list(self._stack))
        self.elements.append(element)
        if tag not in self.VOID:
            self._stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break


def parse(html: str) -> list[_Element]:
    collector = _Collector()
    collector.feed(html)
    return collector.elements


def by_name(html: str, name: str) -> _Element:
    matches = [e for e in parse(html) if e.attrs.get("name") == name]
    assert len(matches) == 1, f"expected one control named {name!r}, found {len(matches)}"
    return matches[0]


def by_id(html: str, element_id: str) -> _Element | None:
    return next((e for e in parse(html) if e.attrs.get("id") == element_id), None)


def described_by(element: _Element) -> list[str]:
    return (element.attrs.get("aria-describedby") or "").split()


# --------------------------------------------------------------------------- fixtures


class Env:
    def __init__(self, flask_app: Flask, settings: Settings, store: ImageStore, ocr: FakeOcrEngine):
        self.app = flask_app
        self.settings = settings
        self.store = store
        self.ocr = ocr
        self.client = flask_app.test_client()

    @property
    def services(self) -> dict:
        return self.app.extensions["invoice"]

    def upload(self, data: bytes = JPEG, filename: str = "factura.jpg"):
        return self.client.post(
            "/uploads",
            data={"file": (io.BytesIO(data), filename)},
            content_type="multipart/form-data",
        )

    def new_draft(self, data: bytes = JPEG) -> str:
        response = self.upload(data)
        assert response.status_code == 200
        match = CONFIRM_ACTION.search(response.get_data(as_text=True))
        assert match is not None
        return match.group(1)

    def tmp_files(self, draft_id: str) -> list[str]:
        return sorted(p.name for p in self.store.tmp.glob(f"{draft_id}.*"))

    def count_entries(self) -> int:
        conn = connect(self.settings.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        finally:
            conn.close()


def build_app(settings: Settings, ocr: FakeOcrEngine) -> tuple[Flask, ImageStore]:
    flask_app = Flask(
        __name__,
        template_folder=str(APP_DIR / "templates"),
        static_folder=str(APP_DIR / "static"),
    )
    security.register(flask_app)
    flask_app.register_blueprint(pages.bp)

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    init_schema(settings.db_path)
    store = ImageStore(settings.images_dir)

    def connect_db():
        return connect(settings.db_path)

    flask_app.extensions["invoice"] = {
        "settings": settings,
        "store": store,
        "drafts": DraftService(store, ocr),
        "entries": EntryService(store, connect_db),
        "dashboard": DashboardService(EntryRepository(), connect_db),
        "purger": DraftPurger(store),
        "ocr_status": ocr.status(),
    }

    @flask_app.errorhandler(404)
    def not_found(_error):  # same behaviour as the handler of app.main (task 12.6)
        return render_template("error.html", status_code=404), 404

    return flask_app, store


@pytest.fixture
def ocr() -> FakeOcrEngine:
    return FakeOcrEngine(text=OCR_TEXT)


@pytest.fixture
def env(settings: Settings, ocr: FakeOcrEngine) -> Env:
    flask_app, store = build_app(settings, ocr)
    return Env(flask_app, settings, store, ocr)


def make_expired_draft(store: ImageStore, draft_id: str = "e" * 32) -> Path:
    path = store.save_temp(draft_id, JPEG, "jpg")
    old = time.time() - 48 * 3600
    os.utime(path, (old, old))
    return path


# --------------------------------------------------------------------------- GET /upload


def test_get_upload_renders_the_camera_form(env: Env):
    response = env.client.get("/upload")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    form = next(e for e in parse(html) if e.tag == "form")
    assert form.attrs["action"] == "/uploads"
    assert form.attrs["method"] == "post"
    assert form.attrs["enctype"] == "multipart/form-data"
    file_input = by_name(html, "file")
    assert file_input.attrs["type"] == "file"
    assert file_input.attrs["accept"] == "image/jpeg,image/png"
    assert file_input.attrs["capture"] == "environment"
    assert f'for="{file_input.attrs["id"]}"' in html
    assert "10 MB" in html
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_get_upload_purges_expired_drafts(env: Env):
    expired = make_expired_draft(env.store)

    assert env.client.get("/upload").status_code == 200

    assert not expired.exists()


# --------------------------------------------------------------------------- POST /uploads


def test_post_without_file_is_400_asking_for_a_file(env: Env):
    response = env.client.post("/uploads", data={}, content_type="multipart/form-data")

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert pages.MSG_MISSING_FILE in html
    assert by_name(html, "file").attrs.get("aria-invalid") == "true"
    assert env.ocr.calls == []


def test_post_with_empty_file_is_400(env: Env):
    response = env.upload(b"", filename="")

    assert response.status_code == 400
    assert pages.MSG_MISSING_FILE in response.get_data(as_text=True)


def test_post_unsupported_format_is_400_listing_formats(env: Env):
    response = env.upload(b"GIF89a" + b"\x00" * 20, filename="factura.jpg")

    assert response.status_code == 400
    assert "Formatos admitidos: JPEG, PNG" in response.get_data(as_text=True)
    assert list(env.store.tmp.iterdir()) == []


def test_post_too_large_is_413_with_max_size(settings: Settings, ocr: FakeOcrEngine):
    small = dataclasses.replace(settings, max_upload_bytes=1024 * 1024)
    flask_app, store = build_app(small, ocr)
    env = Env(flask_app, small, store, ocr)

    response = env.upload(JPEG_MAGIC + b"\x00" * (1024 * 1024))  # max + 3 bytes

    assert response.status_code == 413
    assert "Tamaño máximo: 1 MB" in response.get_data(as_text=True)
    assert ocr.calls == []


def test_post_exactly_max_size_is_accepted(settings: Settings, ocr: FakeOcrEngine):
    small = dataclasses.replace(settings, max_upload_bytes=2048)
    flask_app, store = build_app(small, ocr)
    data = PNG_MAGIC + b"\x00" * (2048 - len(PNG_MAGIC))

    response = flask_app.test_client().post(
        "/uploads", data={"file": (io.BytesIO(data), "x.png")}, content_type="multipart/form-data"
    )

    assert response.status_code == 200
    assert ocr.calls == [data]


def test_valid_upload_shows_review_with_fields_image_and_text(env: Env):
    response = env.upload(PNG, filename="../../evil.gif")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    draft_id = CONFIRM_ACTION.search(html).group(1)
    # Stored under the generated id with the extension detected from the signature.
    assert env.tmp_files(draft_id) == [f"{draft_id}.png", f"{draft_id}.txt"]
    assert env.count_entries() == 0  # nothing saved before confirming (Req. 4.4)

    assert by_name(html, "invoice_date").attrs["value"] == "2025-03-01"
    assert by_name(html, "invoice_date").attrs["type"] == "date"
    assert by_name(html, "supplier").attrs["value"] == "FERRETERIA LOPEZ"
    assert by_name(html, "invoice_number").attrs["value"] == "F-2025-001"
    assert by_name(html, "base_amount").attrs["value"] == "100.00"
    assert by_name(html, "vat_amount").attrs["value"] == "21.00"
    assert by_name(html, "total").attrs["value"] == "121.00"
    selected = [e for e in parse(html) if e.tag == "option" and "selected" in e.attrs]
    assert [e.attrs["value"] for e in selected] == ["gasto"]

    img = next(e for e in parse(html) if e.tag == "img")
    assert img.attrs["src"] == f"/drafts/{draft_id}/image"
    assert img.attrs["alt"]
    assert "Base imponible 100,00 €" in html  # Texto_OCR shown
    assert f'formaction="/drafts/{draft_id}/cancel"' in html
    assert "confirm_mismatch" not in html  # totals add up: no mismatch checkbox


def test_review_highlights_missing_fields(env: Env):
    html = env.upload().get_data(as_text=True)

    for name in ("tax_id", "concept", "category"):
        control = by_name(html, name)
        assert "field--missing" in control.ancestor_classes()
        note_id = f"f-{name}-missing"
        assert note_id in described_by(control)
        assert re.search(rf'id="{note_id}"[^>]*>No detectado<', html)
    for name in ("invoice_date", "supplier", "total", "entry_type"):
        control = by_name(html, name)
        assert "field--missing" not in control.ancestor_classes()
        assert not described_by(control)


def test_review_offers_category_datalist(env: Env):
    html = env.upload().get_data(as_text=True)

    assert by_name(html, "category").attrs["list"] == "categories"
    elements = parse(html)
    datalist = next((e for e in elements if e.attrs.get("id") == "categories"), None)
    assert datalist is not None and datalist.tag == "datalist"
    options = [e.attrs["value"] for e in elements if e.tag == "option" and datalist in e.ancestors]
    assert "Suministros" in options and "Otros" in options


def test_review_escapes_ocr_text_and_values(env: Env):
    env.ocr.text = "<script>alert(1)</script>\nTotal 5,00 €\n"

    html = env.upload().get_data(as_text=True)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    pre = next(e for e in parse(html) if e.tag == "pre")
    assert "ocr-text" in pre.attrs["class"]
    assert by_name(html, "supplier").attrs["value"] == "<script>alert(1)</script>"  # decoded


@pytest.mark.parametrize(
    ("ocr_kwargs", "expected"),
    [
        ({"raises": OcrTimeout("timeout")}, "fallo del OCR"),
        ({"text": "   \n"}, "No se ha detectado texto"),
    ],
)
def test_review_shows_ocr_notices(settings: Settings, ocr_kwargs: dict, expected: str):
    ocr = FakeOcrEngine(**ocr_kwargs)
    flask_app, store = build_app(settings, ocr)
    env = Env(flask_app, settings, store, ocr)

    response = env.upload()

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    alerts = html.split('role="alert"', 1)[1].split("</div>", 1)[0]
    assert expected in alerts
    assert "No hay texto leído" in html
    assert by_name(html, "entry_type")  # empty Borrador is still editable
    assert "field--missing" in by_name(html, "total").ancestor_classes()


def test_review_shows_tax_id_and_mismatch_warnings_upfront(env: Env):
    env.ocr.text = OCR_TEXT.replace("Total 121,00", "Total 130,00") + "NIF 12345678A\n"

    html = env.upload().get_data(as_text=True)

    tax_id = by_name(html, "tax_id")
    assert "f-tax_id-warning" in described_by(tax_id)
    assert tax_id.attrs.get("aria-invalid") is None  # warning, not an error
    checkbox = by_name(html, "confirm_mismatch")
    assert checkbox.attrs["type"] == "checkbox"
    assert "checked" not in checkbox.attrs


def test_post_uploads_purges_expired_drafts(env: Env):
    expired = make_expired_draft(env.store)

    assert env.upload().status_code == 200

    assert not expired.exists()


# --------------------------------------------------------------------------- confirm


def test_confirm_valid_redirects_to_detail_with_saved(env: Env):
    draft_id = env.new_draft()

    response = env.client.post(f"/drafts/{draft_id}/confirm", data=VALID_FORM)

    assert response.status_code == 303
    match = re.fullmatch(r"/entries/(\d+)\?saved=1", response.headers["Location"])
    assert match is not None
    entry = env.services["entries"].get(int(match.group(1)))
    assert entry is not None and entry.supplier == "FERRETERIA LOPEZ"
    assert entry.ocr_text == OCR_TEXT
    assert env.tmp_files(draft_id) == []  # image promoted, text discarded
    assert (env.store.root / entry.image_filename).read_bytes() == JPEG


def test_confirm_invalid_is_422_keeping_values(env: Env):
    draft_id = env.new_draft()
    form = {**VALID_FORM, "total": "abc", "invoice_date": "2025-02-30", "supplier": 'Mi <b>"tienda"</b>'}

    response = env.client.post(f"/drafts/{draft_id}/confirm", data=form)

    assert response.status_code == 422
    html = response.get_data(as_text=True)
    assert by_name(html, "supplier").attrs["value"] == 'Mi <b>"tienda"</b>'
    assert "<b>" not in html
    assert by_name(html, "concept").attrs["value"] == "Tornillos"
    for name in ("total", "invoice_date"):
        control = by_name(html, name)
        assert control.attrs["aria-invalid"] == "true"
        assert f"f-{name}-error" in described_by(control)
        assert "field--error" in control.ancestor_classes()
        assert by_id(html, f"f-{name}-error") is not None
    assert by_name(html, "concept").attrs.get("aria-invalid") is None
    assert "No es un importe válido." in html
    assert "revisa los campos marcados" in html
    # Still the same Borrador: image and OCR text are shown again.
    assert f'src="/drafts/{draft_id}/image"' in html
    assert "Base imponible 100,00 €" in html
    assert env.count_entries() == 0


def test_confirm_mismatch_requires_checkbox_then_saves(env: Env):
    draft_id = env.new_draft()
    form = {**VALID_FORM, "total": "130.00"}

    first = env.client.post(f"/drafts/{draft_id}/confirm", data=form)

    assert first.status_code == 422
    html = first.get_data(as_text=True)
    checkbox = by_name(html, "confirm_mismatch")
    assert checkbox.attrs["type"] == "checkbox"
    assert "totals-warning" in described_by(checkbox)
    assert "no coincide con el total" in html
    assert by_name(html, "total").attrs["value"] == "130.00"
    assert env.count_entries() == 0

    second = env.client.post(f"/drafts/{draft_id}/confirm", data={**form, "confirm_mismatch": "on"})

    assert second.status_code == 303
    assert re.fullmatch(r"/entries/\d+\?saved=1", second.headers["Location"])
    assert env.count_entries() == 1


def test_confirm_keeps_checkbox_checked_when_other_errors(env: Env):
    draft_id = env.new_draft()
    form = {**VALID_FORM, "total": "130.00", "invoice_date": "", "confirm_mismatch": "on"}

    response = env.client.post(f"/drafts/{draft_id}/confirm", data=form)

    assert response.status_code == 422
    assert "checked" in by_name(response.get_data(as_text=True), "confirm_mismatch").attrs


def test_confirm_unknown_draft_is_404(env: Env):
    response = env.client.post(f"/drafts/{UNKNOWN_DRAFT}/confirm", data=VALID_FORM)

    assert response.status_code == 404
    assert "Página no encontrada" in response.get_data(as_text=True)


def test_confirm_save_error_is_500_and_draft_kept(env: Env, monkeypatch: pytest.MonkeyPatch):
    draft_id = env.new_draft()

    def failing_promote(_draft_id: str) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(env.store, "promote", failing_promote)

    response = env.client.post(f"/drafts/{draft_id}/confirm", data=VALID_FORM)

    assert response.status_code == 500
    html = response.get_data(as_text=True)
    assert pages.MSG_SAVE_ERROR in html
    assert by_name(html, "concept").attrs["value"] == "Tornillos"
    assert env.count_entries() == 0
    assert env.tmp_files(draft_id) == [f"{draft_id}.jpg", f"{draft_id}.txt"]

    monkeypatch.undo()  # the Usuario can retry
    retry = env.client.post(f"/drafts/{draft_id}/confirm", data=VALID_FORM)
    assert retry.status_code == 303


# --------------------------------------------------------------------------- cancel


def test_cancel_discards_draft_and_redirects_to_upload(env: Env):
    draft_id = env.new_draft()
    assert env.tmp_files(draft_id)

    response = env.client.post(f"/drafts/{draft_id}/cancel")

    assert response.status_code == 303
    assert response.headers["Location"] == "/upload"
    assert env.tmp_files(draft_id) == []
    assert env.client.post(f"/drafts/{draft_id}/confirm", data=VALID_FORM).status_code == 404


def test_cancel_unknown_draft_still_redirects(env: Env):
    response = env.client.post(f"/drafts/{UNKNOWN_DRAFT}/cancel")

    assert response.status_code == 303
    assert response.headers["Location"] == "/upload"


@pytest.mark.parametrize("bad_id", ["abc", "A" * 32, "g" * 32, "a" * 33, "..%2F..%2Fdb", "a" * 31 + "%0A"])
@pytest.mark.parametrize("action", ["confirm", "cancel"])
def test_invalid_draft_id_is_404(env: Env, bad_id: str, action: str):
    draft_id = env.new_draft()

    response = env.client.post(f"/drafts/{bad_id}/{action}", data=VALID_FORM)

    assert response.status_code == 404
    assert env.tmp_files(draft_id)  # nothing discarded
    assert env.count_entries() == 0


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [
        (10 * 1024 * 1024, "10 MB"),
        (1536 * 1024, "1,5 MB"),
        (4096, "4 KB"),
        (500, "500 bytes"),
    ],
)
def test_format_size(num_bytes: int, expected: str):
    assert pages.format_size(num_bytes) == expected
    assert pages.too_large_message(num_bytes) == f"Tamaño máximo: {expected}"


def test_render_upload_for_413_handler(env: Env):
    with env.app.test_request_context("/uploads", method="POST"):
        body, status = pages.render_upload(pages.too_large_message(10 * 1024 * 1024), 413)

    assert status == 413
    assert "Tamaño máximo: 10 MB" in body
