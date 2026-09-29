"""Motor_OCR: interfaz ``OcrEngine`` e implementación con Tesseract.

El reconocimiento se hace en local con ``pytesseract`` (Tesseract, idioma
``spa``) sin enviar la imagen ni el texto fuera del proceso. Las pruebas usan
``tests.fakes.FakeOcrEngine`` para no depender del binario de Tesseract.

Requirements: 2.1, 2.2, 2.3, 2.4, 15.2.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pytesseract
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

DEFAULT_LANG = "spa"
DEFAULT_TIMEOUT_S = 60

# Fragmentos (en minúsculas) con los que Tesseract indica que no puede cargar
# el idioma pedido: "Failed loading language 'spa'", "Error opening data file
# .../spa.traineddata", "Tesseract couldn't load any languages!".
_MISSING_LANGUAGE_MARKERS = ("language", "traineddata")


class OcrError(Exception):
    """Error genérico del Motor_OCR."""


class OcrTimeout(OcrError):
    """El reconocimiento no terminó dentro del tiempo máximo (Req. 2.3)."""


class OcrUnavailable(OcrError):
    """Tesseract o el idioma requerido no están disponibles (Req. 2.4)."""


@dataclass(frozen=True)
class OcrStatus:
    """Estado del Motor_OCR para ``/health``.

    ``available`` es ``True`` solo si Tesseract responde y el idioma requerido
    está instalado. ``detail`` describe el motivo cuando no está disponible.
    """

    available: bool
    languages: tuple[str, ...] = ()
    detail: str | None = None


@runtime_checkable
class OcrEngine(Protocol):
    def extract_text(self, image_bytes: bytes) -> str: ...

    def status(self) -> OcrStatus: ...


class TesseractOcrEngine:
    """``OcrEngine`` respaldado por ``pytesseract``."""

    def __init__(self, lang: str = DEFAULT_LANG, timeout_s: int = DEFAULT_TIMEOUT_S) -> None:
        self.lang = lang
        self.timeout_s = timeout_s

    def extract_text(self, image_bytes: bytes) -> str:
        """Devuelve el Texto_OCR de ``image_bytes``.

        La orientación EXIF se corrige solo en la copia en memoria usada para el
        OCR; los bytes recibidos no se modifican.

        Raises:
            OcrTimeout: Tesseract supera ``timeout_s`` segundos.
            OcrUnavailable: falta el binario de Tesseract o el idioma ``lang``.
            OcrError: la imagen no se puede decodificar u otro fallo de Tesseract.
        """
        try:
            with Image.open(io.BytesIO(image_bytes)) as original:
                original.load()
                image = ImageOps.exif_transpose(original)
                return pytesseract.image_to_string(image, lang=self.lang, timeout=self.timeout_s)
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrUnavailable(str(exc)) from exc
        except pytesseract.TesseractError as exc:
            message = str(exc)
            if any(marker in message.lower() for marker in _MISSING_LANGUAGE_MARKERS):
                raise OcrUnavailable(message) from exc
            raise OcrError(message) from exc
        except RuntimeError as exc:
            # pytesseract lanza RuntimeError("Tesseract process timeout") al
            # superar el timeout (y mata el proceso de Tesseract).
            if "timeout" in str(exc).lower():
                raise OcrTimeout(str(exc)) from exc
            raise OcrError(str(exc)) from exc
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            # UnidentifiedImageError es subclase de OSError: imagen ilegible.
            raise OcrError(f"No se pudo procesar la imagen: {exc}") from exc

    def status(self) -> OcrStatus:
        """Comprueba si Tesseract responde y tiene el idioma ``lang``.

        Nunca lanza excepciones: la Aplicación debe arrancar aunque Tesseract no
        esté instalado (``/health`` informará ``degraded``).
        """
        try:
            languages = tuple(pytesseract.get_languages())
        except Exception as exc:  # noqa: BLE001 - el arranque no puede fallar por el OCR
            logger.warning("Tesseract no disponible: %s", exc)
            return OcrStatus(available=False, detail=str(exc) or type(exc).__name__)
        if self.lang not in languages:
            logger.warning("Idioma de Tesseract %r no instalado (disponibles: %s)", self.lang, languages)
            return OcrStatus(
                available=False,
                languages=languages,
                detail=f"Idioma '{self.lang}' no instalado",
            )
        return OcrStatus(available=True, languages=languages)
