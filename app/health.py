"""Comprobaciones del Endpoint_Salud (``GET /health``), independientes de Flask.

``check_health`` devuelve ``(cuerpo, código)`` con la forma del diseño::

    {"status": "ok|degraded|error",
     "database": {"ok": true},
     "images": {"ok": true, "writable": true},
     "ocr": {"ok": true, "languages": ["spa", "eng"]}}

- Base_Datos: ``SELECT 1`` con ``sqlite3.connect(timeout=1)``. Se abre en modo ``rw`` para
  no crear un fichero vacío si la base de datos ha desaparecido.
- Almacén_Imágenes: se crea y se borra ``tmp/.health-{uuid}``.
- Motor_OCR: se usa el ``OcrStatus`` cacheado en el arranque; nunca se invoca Tesseract
  aquí, para responder en menos de 2 s (13.1).
- Código: 200 si la BD y el almacén están bien (``degraded`` si además el OCR está caído);
  503 en otro caso (13.2, 13.3).

Ninguna función de este módulo lanza excepciones: un fallo se refleja como ``ok: false``.

Requirements: 13.1, 13.2, 13.3.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from os import PathLike
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.ocr import OcrStatus

logger = logging.getLogger(__name__)

DB_TIMEOUT_SECONDS = 1
HEALTH_FILE_PREFIX = ".health-"

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_ERROR = "error"

HTTP_OK = 200
HTTP_SERVICE_UNAVAILABLE = 503


def check_database(db_path: str | PathLike[str]) -> dict[str, bool]:
    """``{"ok": True}`` si ``SELECT 1`` responde sobre la base de datos existente."""
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=DB_TIMEOUT_SECONDS)) as conn:
            ok = conn.execute("SELECT 1").fetchone() == (1,)
    except Exception as exc:  # noqa: BLE001 - /health nunca debe fallar
        logger.warning("Health: base de datos no accesible: %s", exc)
        return {"ok": False}
    return {"ok": ok}


def check_images(tmp_dir: str | PathLike[str]) -> dict[str, bool]:
    """Crea y borra ``tmp_dir/.health-{uuid}``; ``ok``/``writable`` indican si fue posible."""
    probe = Path(tmp_dir) / f"{HEALTH_FILE_PREFIX}{uuid4().hex}"
    try:
        with open(probe, "xb") as fh:
            fh.write(b"ok")
        probe.unlink()
    except Exception as exc:  # noqa: BLE001 - /health nunca debe fallar
        logger.warning("Health: almacén de imágenes sin escritura: %s", exc)
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass  # el sondeo ya falló; purge_expired_drafts limpiará el resto
        return {"ok": False, "writable": False}
    return {"ok": True, "writable": True}


def ocr_report(status: OcrStatus) -> dict[str, Any]:
    """Traduce el ``OcrStatus`` cacheado a ``{"ok": ..., "languages": [...]}``."""
    return {"ok": bool(status.available), "languages": list(status.languages)}


def overall_status(database_ok: bool, images_ok: bool, ocr_ok: bool) -> tuple[str, int]:
    """``(status, código HTTP)``: la BD y el almacén deciden el código; el OCR solo degrada."""
    if not (database_ok and images_ok):
        return STATUS_ERROR, HTTP_SERVICE_UNAVAILABLE
    if not ocr_ok:
        return STATUS_DEGRADED, HTTP_OK
    return STATUS_OK, HTTP_OK


def check_health(
    db_path: str | PathLike[str],
    tmp_dir: str | PathLike[str],
    ocr_status: OcrStatus,
) -> tuple[dict[str, Any], int]:
    """Ejecuta las comprobaciones y devuelve el cuerpo JSON del diseño y su código HTTP."""
    database = check_database(db_path)
    images = check_images(tmp_dir)
    ocr = ocr_report(ocr_status)
    status, code = overall_status(database["ok"], images["ok"], ocr["ok"])
    body = {"status": status, "database": database, "images": images, "ocr": ocr}
    return body, code
