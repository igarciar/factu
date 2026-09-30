"""Motor_OCR: interfaz ``OcrEngine`` e implementación con Tesseract.

El reconocimiento se hace en local con ``pytesseract`` (Tesseract, idioma
``spa``) sin enviar la imagen ni el texto fuera del proceso. Las pruebas usan
``tests.fakes.FakeOcrEngine`` para no depender del binario de Tesseract.

Requirements: 2.1, 2.2, 2.3, 2.4, 15.2.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pytesseract
import requests
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


class VisionModelOcrEngine:
    """``OcrEngine`` respaldado por Ollama con modelo de visión (qwen-vl o llava).
    
    Env: by default, connects to http://127.0.0.1:11434 (Ollama API endpoint).
    Modelo: qwen-vl:7b-q4 (quantized, ~5GB) con fallback a llava:7b-q4.
    """

    def __init__(
        self,
        ollama_api_url: str = "http://127.0.0.1:11434",
        model: str = "qwen-vl:7b-q4",
        fallback_model: str = "llava:7b-q4",
        timeout_s: int = 120,
    ) -> None:
        self.ollama_api_url = ollama_api_url.rstrip("/")
        self.model = model
        self.fallback_model = fallback_model
        self.timeout_s = timeout_s

    def extract_text(self, image_bytes: bytes) -> str:
        """Devuelve el Texto_OCR de ``image_bytes`` usando Ollama con modelo de visión.
        
        Encodes the image as base64 and sends to the Ollama API with a prompt:
        'Extract all text from this invoice. Focus on table data, amounts, dates, and 
        identifiers. Output plain text only, preserving layout and structure.'
        
        Raises:
            OcrTimeout: Ollama supera ``timeout_s`` segundos o no responde.
            OcrUnavailable: Ollama API no es accesible.
            OcrError: fallo de procesamiento.
        """
        # Encode image as base64
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img.load()
                image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        except Exception as exc:
            raise OcrError(f"No se pudo procesar la imagen: {exc}") from exc

        prompt = (
            "Extract all text from this invoice. Focus on table data, amounts, dates, and identifiers. "
            "Output plain text only, preserving layout and structure."
        )

        # Try primary model first
        models_to_try = [self.model, self.fallback_model]
        last_error = None

        for model_name in models_to_try:
            try:
                response = requests.post(
                    f"{self.ollama_api_url}/api/generate",
                    json={
                        "model": model_name,
                        "prompt": prompt,
                        "images": [image_base64],
                        "stream": False,
                    },
                    timeout=self.timeout_s,
                )
                response.raise_for_status()
                data = response.json()
                if "response" in data:
                    text = data["response"].strip()
                    if text:
                        return text
                    raise OcrError(f"Modelo {model_name} devolvió respuesta vacía")
            except requests.Timeout as exc:
                last_error = OcrTimeout(f"Ollama {model_name} timeout: {exc}")
                logger.warning("Ollama %s timeout", model_name)
                # Try fallback if available
                if model_name == self.model and self.fallback_model != model_name:
                    continue
                raise last_error from exc
            except requests.ConnectionError as exc:
                last_error = OcrUnavailable(f"Ollama API no es accesible: {exc}")
                logger.warning("Ollama API no accesible: %s", exc)
                # Try fallback
                if model_name == self.model and self.fallback_model != model_name:
                    continue
                raise last_error from exc
            except requests.RequestException as exc:
                error_msg = str(exc)
                if "404" in error_msg or "model not found" in error_msg.lower():
                    # Model not available, try fallback
                    if model_name == self.model and self.fallback_model != model_name:
                        logger.warning("Modelo %s no disponible, probando fallback", model_name)
                        continue
                    last_error = OcrUnavailable(f"Modelo {model_name} no disponible: {exc}")
                    raise last_error from exc
                last_error = OcrError(f"Error Ollama: {exc}")
                raise last_error from exc

        if last_error:
            raise last_error

        raise OcrError("No se pudo obtener respuesta de Ollama")

    def status(self) -> OcrStatus:
        """Comprueba si Ollama API está accesible y tiene el modelo disponible."""
        try:
            response = requests.get(
                f"{self.ollama_api_url}/api/tags",
                timeout=2,
            )
            response.raise_for_status()
            data = response.json()
            models = tuple(m.get("name", "") for m in data.get("models", []))
            
            # Check if primary model is available
            if any(m.startswith(self.model) for m in models):
                return OcrStatus(available=True, languages=models)
            
            # Check fallback
            if any(m.startswith(self.fallback_model) for m in models):
                logger.warning("Modelo principal %s no disponible, usando fallback", self.model)
                return OcrStatus(available=True, languages=models)
            
            return OcrStatus(
                available=False,
                languages=models,
                detail=f"Modelos {self.model} o {self.fallback_model} no están pre-descargados",
            )
        except requests.RequestException as exc:
            logger.warning("Ollama API no accesible: %s", exc)
            return OcrStatus(
                available=False,
                detail=f"Ollama API no accesible: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error comprobando Ollama: %s", exc)
            return OcrStatus(
                available=False,
                detail=str(exc) or type(exc).__name__,
            )


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
