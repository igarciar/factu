"""Prueba de integración del Motor_OCR con Tesseract real (idioma ``spa``).

Excluida de la ejecución por defecto (``addopts = -m 'not tesseract'``). Se ejecuta a mano dentro
del contenedor, donde están instalados ``tesseract-ocr`` y ``tesseract-ocr-spa``::

    pytest -m tesseract

Si Tesseract o el idioma ``spa`` no están disponibles, la prueba se omite con el motivo que
devuelve ``TesseractOcrEngine.status()``.

Requirements: 2.1, 2.2.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.extractor import extract
from app.ocr import TesseractOcrEngine

pytestmark = pytest.mark.tesseract

FONT_SIZE = 40
# DejaVuSans existe en la imagen Debian del contenedor; las rutas de Windows solo sirven para
# ejecutar la prueba en local si alguien tiene Tesseract instalado.
FONT_CANDIDATES = (
    "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "arial.ttf",
)

INVOICE_LINES = (
    "FERRETERIA LOPEZ S.L.",
    "NIF: 12345678Z",
    "Factura n: F-2024-001",
    "Fecha: 15/03/2024",
    "",
    "Concepto: Material de oficina",
    "",
    "Base imponible: 100,00 EUR",
    "IVA: 21,00 EUR",
    "Total: 121,00 EUR",
)


def _load_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default(size=FONT_SIZE)  # Pillow >= 10.1


def _synthetic_invoice_png() -> bytes:
    """Factura en español, texto negro sobre fondo blanco, letra grande y márgenes amplios."""
    font = _load_font()
    line_height = int(FONT_SIZE * 1.6)
    margin = 60
    image = Image.new("RGB", (1400, margin * 2 + line_height * len(INVOICE_LINES)), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(INVOICE_LINES):
        draw.text((margin, margin + index * line_height), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def engine() -> TesseractOcrEngine:
    ocr = TesseractOcrEngine(lang="spa", timeout_s=60)
    status = ocr.status()
    if not status.available:
        pytest.skip(f"Tesseract con idioma 'spa' no disponible: {status.detail}")
    return ocr


def test_real_tesseract_reads_synthetic_spanish_invoice(engine: TesseractOcrEngine) -> None:
    image_bytes = _synthetic_invoice_png()

    text = engine.extract_text(image_bytes)

    # Req. 2.1: Texto_OCR obtenido en local con el idioma español.
    folded = text.lower()
    assert "factura" in folded, text
    assert "total" in folded, text
    assert "121,00" in text, text

    # Req. 2.2: el Texto_OCR alimenta al extractor, que detecta al menos el importe total.
    draft = extract(text)
    assert draft.values["total"] == "121.00", (draft.values, text)
    assert "total" not in draft.missing
