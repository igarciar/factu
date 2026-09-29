"""Blueprint ``health``: ``GET /health`` (Endpoint_Salud).

Returns the JSON of the design built by ``app.health.check_health`` over the configured
database, the ``tmp/`` directory of the Almacén_Imágenes and the ``OcrStatus`` cached at
start-up (Tesseract is never invoked here, so the answer takes well under 2 s). The code is
200 (``ok``/``degraded``) or 503 (``error``). The response is never cached.

Requirements: 13.1, 13.2, 13.3.
"""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify

from app.health import check_health

bp = Blueprint("health", __name__)


@bp.get("/health")
def health() -> tuple[Response, int]:
    services = current_app.extensions["invoice"]
    body, code = check_health(
        services["settings"].db_path, services["store"].tmp, services["ocr_status"]
    )
    response = jsonify(body)
    response.headers["Cache-Control"] = "no-store"
    return response, code
