"""Pruebas unitarias de ``app.ocr`` con ``pytesseract`` simulado (Req. 2.1, 2.3, 2.4, 15.2)."""

from __future__ import annotations

import io

import pytest
import pytesseract
from PIL import Image

from app.ocr import (
    OcrEngine,
    OcrError,
    OcrStatus,
    OcrTimeout,
    OcrUnavailable,
    TesseractOcrEngine,
)
from tests.fakes import FakeOcrEngine

MISSING_TESSERACT = r"C:\no-existe\tesseract-inexistente.exe"


def _png_bytes(size: tuple[int, int] = (4, 2)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_orientation(size: tuple[int, int], orientation: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation  # Orientation
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def _raise(exc: BaseException):
    def _fn(*args, **kwargs):
        raise exc

    return _fn


# --- extract_text -----------------------------------------------------------


def test_extract_text_calls_tesseract_with_spa_and_60s_timeout(monkeypatch):
    calls = []

    def fake_image_to_string(image, **kwargs):
        calls.append((image, kwargs))
        return "Factura nº 1\nTotal 12,00 €"

    monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)

    text = TesseractOcrEngine().extract_text(_png_bytes())

    assert text == "Factura nº 1\nTotal 12,00 €"
    assert len(calls) == 1
    image, kwargs = calls[0]
    assert isinstance(image, Image.Image)
    assert kwargs == {"lang": "spa", "timeout": 60}


def test_extract_text_applies_exif_orientation_only_for_ocr(monkeypatch):
    seen_sizes = []
    monkeypatch.setattr(
        pytesseract, "image_to_string", lambda image, **kw: seen_sizes.append(image.size) or ""
    )
    data = _jpeg_with_orientation((8, 4), orientation=6)  # 90° → se ve en vertical
    original = bytes(data)

    TesseractOcrEngine().extract_text(data)

    assert seen_sizes == [(4, 8)]
    assert data == original


def test_runtime_timeout_becomes_ocr_timeout(monkeypatch):
    monkeypatch.setattr(
        pytesseract, "image_to_string", _raise(RuntimeError("Tesseract process timeout"))
    )
    with pytest.raises(OcrTimeout):
        TesseractOcrEngine().extract_text(_png_bytes())


def test_other_runtime_error_becomes_generic_ocr_error(monkeypatch):
    monkeypatch.setattr(pytesseract, "image_to_string", _raise(RuntimeError("boom")))
    with pytest.raises(OcrError) as info:
        TesseractOcrEngine().extract_text(_png_bytes())
    assert not isinstance(info.value, (OcrTimeout, OcrUnavailable))


def test_tesseract_not_found_becomes_ocr_unavailable(monkeypatch):
    monkeypatch.setattr(pytesseract, "image_to_string", _raise(pytesseract.TesseractNotFoundError()))
    with pytest.raises(OcrUnavailable):
        TesseractOcrEngine().extract_text(_png_bytes())


@pytest.mark.parametrize(
    "message",
    [
        "Failed loading language 'spa'",
        "Error opening data file /usr/share/tesseract-ocr/5/tessdata/spa.traineddata",
    ],
)
def test_missing_language_becomes_ocr_unavailable(monkeypatch, message):
    monkeypatch.setattr(pytesseract, "image_to_string", _raise(pytesseract.TesseractError(1, message)))
    with pytest.raises(OcrUnavailable):
        TesseractOcrEngine().extract_text(_png_bytes())


def test_other_tesseract_error_becomes_generic_ocr_error(monkeypatch):
    monkeypatch.setattr(
        pytesseract, "image_to_string", _raise(pytesseract.TesseractError(1, "Image too small"))
    )
    with pytest.raises(OcrError) as info:
        TesseractOcrEngine().extract_text(_png_bytes())
    assert not isinstance(info.value, OcrUnavailable)


def test_undecodable_image_becomes_ocr_error(monkeypatch):
    monkeypatch.setattr(pytesseract, "image_to_string", _raise(AssertionError("no debe llamarse")))
    with pytest.raises(OcrError):
        TesseractOcrEngine().extract_text(b"\x89PNG\r\n\x1a\n" + b"basura")


def test_missing_binary_real_pytesseract_becomes_ocr_unavailable(monkeypatch):
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", MISSING_TESSERACT)
    with pytest.raises(OcrUnavailable):
        TesseractOcrEngine().extract_text(_png_bytes())


# --- status -----------------------------------------------------------------


def test_status_available_when_spa_installed(monkeypatch):
    monkeypatch.setattr(pytesseract, "get_languages", lambda *a, **kw: ["eng", "osd", "spa"])
    assert TesseractOcrEngine().status() == OcrStatus(
        available=True, languages=("eng", "osd", "spa")
    )


def test_status_unavailable_without_spa(monkeypatch):
    monkeypatch.setattr(pytesseract, "get_languages", lambda *a, **kw: ["eng", "osd"])
    status = TesseractOcrEngine().status()
    assert status.available is False
    assert status.languages == ("eng", "osd")
    assert "spa" in status.detail


@pytest.mark.parametrize(
    "exc",
    [pytesseract.TesseractNotFoundError(), OSError("sin permisos"), RuntimeError()],
)
def test_status_never_raises(monkeypatch, exc):
    monkeypatch.setattr(pytesseract, "get_languages", _raise(exc))
    status = TesseractOcrEngine().status()
    assert status.available is False
    assert status.languages == ()
    assert status.detail


def test_status_with_missing_binary_real_pytesseract(monkeypatch):
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", MISSING_TESSERACT)
    status = TesseractOcrEngine().status()
    assert status.available is False


# --- FakeOcrEngine ----------------------------------------------------------


def test_fake_engine_returns_text_records_calls_and_status():
    custom = OcrStatus(available=False, detail="caído")
    fake = FakeOcrEngine(text="hola", status=custom)
    assert isinstance(fake, OcrEngine)
    assert isinstance(TesseractOcrEngine(), OcrEngine)
    assert fake.extract_text(b"abc") == "hola"
    assert fake.calls == [b"abc"]
    assert fake.status() is custom
    assert FakeOcrEngine().extract_text(b"x") == ""
    assert FakeOcrEngine().status().available is True


@pytest.mark.parametrize("raises", [OcrTimeout, OcrUnavailable("sin spa")])
def test_fake_engine_raises(raises):
    with pytest.raises(OcrError):
        FakeOcrEngine(raises=raises).extract_text(b"x")
