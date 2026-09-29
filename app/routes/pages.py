"""Blueprint ``pages``: Portada, upload → review → confirm/cancel of a Borrador.

Routes (design "Rutas HTTP"):

- ``GET /?recent_page=N``: Portada (``home.html``) with the Resumen_Gastos and the
  Página_Recientes ``N`` (normalized with ``parse_recent_page``; 1 if invalid).
- ``GET /recent?page=N``: with ``HX-Request`` the fragment ``_recent_rows.html`` (rows plus
  the out-of-band ``#recent-more``); without it, 303 → ``/?recent_page=N``.
- ``GET /upload``: upload form (``upload.html``).
- ``POST /uploads``: validates the file by signature and size, creates the Borrador and shows
  ``review.html`` (200); 400/413 re-render ``upload.html`` with the error.
- ``POST /drafts/<draft_id>/confirm``: 303 → ``/entries/<id>?saved=1`` · 422 ``review.html``
  with errors and the submitted values · 500 ``review.html`` with the save error · 404.
- ``POST /drafts/<draft_id>/cancel``: discards the Borrador, 303 → ``/upload``.

``GET /``, ``GET /upload`` and ``POST /uploads`` call ``DraftPurger.maybe_purge()`` first
(Req. 4.6).

Services come from ``current_app.extensions["invoice"]`` (built by ``app.main.create_app``).
Not-found cases use ``abort(404)``; the application's 404 handler renders ``error.html``.

Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 4.1, 4.2, 4.3, 4.4, 4.5, 5.5, 5.7, 6.6, 7.2, 12.4,
14.7, 16.1, 16.6-16.19.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
)
from werkzeug.wrappers import Response

from app.formatting import parse_recent_page
from app.models import ConfirmResult, DraftView
from app.services import DraftNotFound, SaveError, is_mismatch_confirmed
from app.storage import DRAFT_ID
from app.uploads import FileTooLarge, MissingFile, UnsupportedFormat, validate_upload
from app.validation import validate_entry

logger = logging.getLogger(__name__)

bp = Blueprint("pages", __name__)

MSG_MISSING_FILE = "Selecciona un fichero de imagen"
MSG_UNSUPPORTED_FORMAT = "Formatos admitidos: JPEG, PNG"
MSG_SAVE_ERROR = "No se ha podido guardar, inténtalo de nuevo"

_KIB = 1024
_MIB = 1024 * 1024


def _services() -> Mapping[str, Any]:
    return current_app.extensions["invoice"]


def format_size(num_bytes: int) -> str:
    """Human size in Spanish: ``10485760`` → ``"10 MB"``, ``1572864`` → ``"1,5 MB"``.

    Values under 1 MB are shown in KB (or bytes) so a small configured maximum never reads
    as ``"0 MB"``.
    """
    for unit_size, unit in ((_MIB, "MB"), (_KIB, "KB")):
        if num_bytes >= unit_size:
            text = f"{num_bytes / unit_size:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            return f"{text} {unit}"
    return f"{num_bytes} bytes"


def too_large_message(max_bytes: int) -> str:
    """Message for ``FileTooLarge`` and for Werkzeug's ``RequestEntityTooLarge`` (Req. 1.5)."""
    return f"Tamaño máximo: {format_size(max_bytes)}"


def render_upload(error: str | None = None, status: int = 200) -> tuple[str, int]:
    """Render ``upload.html``; also used by the application's 413 handler (task 12.6).

    Template variables: ``error`` (message or ``None``) and ``max_size`` (e.g. ``"10 MB"``).
    """
    settings = _services()["settings"]
    return (
        render_template(
            "upload.html", error=error, max_size=format_size(settings.max_upload_bytes)
        ),
        status,
    )


def _require_draft_id(draft_id: str) -> None:
    # fullmatch: DRAFT_ID ends with "$", which alone would also accept a trailing "\n".
    if DRAFT_ID.fullmatch(draft_id) is None:
        logger.info("Identificador de borrador no válido")
        abort(404)


def _render_review(
    draft_id: str,
    view: DraftView | None,
    values: Mapping[str, str],
    *,
    errors: Mapping[str, str] | None = None,
    warnings: Mapping[str, str] | None = None,
    mismatch_confirmed: bool = False,
    save_error: str | None = None,
    status: int = 200,
) -> tuple[str, int]:
    """Render ``review.html``.

    ``view`` gives the Texto_OCR, the fields not detected and the OCR notice; it may be
    ``None`` if the Borrador vanished after a failed save (the form is still redisplayed).
    """
    draft = view.draft if view is not None else None
    return (
        render_template(
            "review.html",
            draft_id=draft_id,
            image_url=f"/drafts/{draft_id}/image",
            values=values,
            missing=draft.missing if draft is not None else frozenset(),
            notice=draft.notice if draft is not None else None,
            ocr_text=view.ocr_text if view is not None else "",
            errors=errors or {},
            warnings=warnings or {},
            mismatch_confirmed=mismatch_confirmed,
            save_error=save_error,
            categories=_services()["entries"].category_names(),
        ),
        status,
    )


@bp.get("/")
def home() -> tuple[str, int]:
    """Portada: Resumen_Gastos, Últimas_Facturas and the "Subir factura" button (Req. 16)."""
    services = _services()
    services["purger"].maybe_purge()
    page = parse_recent_page(request.args.get("recent_page"))  # 16.15
    dashboard = services["dashboard"]
    return (
        render_template(
            "home.html", summary=dashboard.expense_summary(), recent=dashboard.recent(page)
        ),
        200,
    )


@bp.get("/recent")
def recent_rows() -> Response:
    """Next Página_Recientes for "Ver siguientes" (Req. 16.10-16.14).

    HTMX appends the returned rows to ``#recent-rows`` and replaces ``#recent-more``
    out-of-band. A plain request (no ``HX-Request``) never gets a loose fragment: it is
    redirected to the full Portada with that page.
    """
    page = parse_recent_page(request.args.get("page"))
    if not request.headers.get("HX-Request"):
        response = redirect(f"/?recent_page={page}", code=303)
    else:
        recent = _services()["dashboard"].recent(page)
        response = make_response(render_template("_recent_rows.html", recent=recent, oob=True))
    # The same URL answers differently depending on the header.
    response.vary.add("HX-Request")
    return response


@bp.get("/upload")
def upload_form() -> tuple[str, int]:
    """Upload form (Req. 1.1, 12.4)."""
    _services()["purger"].maybe_purge()
    return render_upload()


@bp.post("/uploads")
def upload() -> tuple[str, int]:
    """Validate the upload, create the Borrador and show the review form (Req. 1.2-1.6, 4.1)."""
    services = _services()
    services["purger"].maybe_purge()
    max_bytes = services["settings"].max_upload_bytes

    file = request.files.get("file")
    # Read at most one byte more than allowed: enough to detect FileTooLarge without
    # loading an arbitrarily large body into memory.
    data = file.stream.read(max_bytes + 1) if file is not None else None
    try:
        fmt = validate_upload(data, max_bytes)
    except MissingFile:
        logger.info("Subida rechazada: sin fichero")
        return render_upload(MSG_MISSING_FILE, 400)
    except UnsupportedFormat:
        logger.info("Subida rechazada: formato no admitido")
        return render_upload(MSG_UNSUPPORTED_FORMAT, 400)
    except FileTooLarge:
        logger.info("Subida rechazada: supera %d bytes", max_bytes)
        return render_upload(too_large_message(max_bytes), 413)

    view = services["drafts"].create(data, fmt)
    values = view.draft.values
    # Non-blocking warnings (mismatch, NIF/CIF) are shown from the start (Req. 5.5, 5.6);
    # errors are only shown after the Usuario submits the form.
    initial = validate_entry(values, mismatch_confirmed=False)
    return _render_review(view.draft_id, view, values, warnings=initial.warnings)


@bp.post("/drafts/<draft_id>/confirm")
def confirm(draft_id: str) -> Response | tuple[str, int]:
    """Save the Borrador as an Apunte (Req. 4.4, 5.5, 5.7, 6.6)."""
    _require_draft_id(draft_id)
    services = _services()
    form = request.form
    mismatch_confirmed = is_mismatch_confirmed(form)
    try:
        result: ConfirmResult = services["entries"].confirm(draft_id, form, mismatch_confirmed)
    except DraftNotFound:
        logger.info("Borrador %s inexistente o caducado", draft_id)
        abort(404)
    except SaveError:
        # EntryService already rolled back, moved the image back and logged the exception.
        return _render_review(
            draft_id,
            services["drafts"].get(draft_id),
            {str(key): str(value) for key, value in form.items()},
            mismatch_confirmed=mismatch_confirmed,
            save_error=MSG_SAVE_ERROR,
            status=500,
        )

    if result.saved:
        return redirect(f"/entries/{result.entry_id}?saved=1", code=303)
    return _render_review(
        draft_id,
        services["drafts"].get(draft_id),
        result.form,
        errors=result.errors,
        warnings=result.warnings,
        mismatch_confirmed=mismatch_confirmed,
        status=422,
    )


@bp.post("/drafts/<draft_id>/cancel")
def cancel(draft_id: str) -> Response:
    """Discard the Borrador and its temporary files (Req. 4.5)."""
    _require_draft_id(draft_id)
    _services()["drafts"].cancel(draft_id)
    return redirect("/upload", code=303)
