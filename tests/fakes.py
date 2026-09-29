"""Dobles de prueba compartidos (Req. 15.2)."""

from __future__ import annotations

from app.ocr import OcrStatus

DEFAULT_FAKE_STATUS = OcrStatus(available=True, languages=("spa",))


class FakeOcrEngine:
    """``OcrEngine`` sin Tesseract: devuelve un texto fijo o lanza una excepción.

    Args:
        text: Texto_OCR devuelto por ``extract_text`` (``None`` → ``""``).
        raises: excepción (instancia o clase) que lanza ``extract_text``,
            normalmente ``OcrTimeout`` u ``OcrUnavailable``.
        status: valor devuelto por ``status()``.
    """

    def __init__(
        self,
        text: str | None = None,
        raises: BaseException | type[BaseException] | None = None,
        status: OcrStatus = DEFAULT_FAKE_STATUS,
    ) -> None:
        self.text = text
        self.raises = raises
        self._status = status
        self.calls: list[bytes] = []

    def extract_text(self, image_bytes: bytes) -> str:
        self.calls.append(image_bytes)
        if self.raises is not None:
            raise self.raises
        return self.text or ""

    def status(self) -> OcrStatus:
        return self._status
