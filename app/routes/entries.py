"""Blueprint ``entries``: listado, detalle, edición y borrado de Apuntes.

Rutas (design.md, "Rutas HTTP"):

- ``GET /entries?type=&category=&date_from=&date_to=&page=``: listado filtrado y paginado
  (20 por página). Con ``HX-Request`` (y sin ``HX-History-Restore-Request``) devuelve solo el
  parcial ``_entries_table.html`` (Req. 8.1-8.4).
- ``GET /entries/<id>``: detalle; con ``?saved=1`` muestra el mensaje de guardado (6.6, 8.5).
- ``GET/POST /entries/<id>/edit``: edición con las validaciones del Requisito 5 (9.1).
- ``GET /entries/<id>/delete``: página de confirmación (9.2); ``POST`` borra y redirige con
  303 a ``/entries`` (9.3, 9.4).

Un Apunte inexistente responde 404 con ``abort(404)`` (8.6); la página ``error.html`` la pinta
el manejador global de ``create_app``.

Parámetros del listado que no son válidos (tipo desconocido, fecha que no es ``aaaa-mm-dd``
de calendario, ``page`` no numérica o fuera de ``1..100000``) se ignoran y ``page`` pasa a 1,
con el mismo criterio que la Portada (``parse_recent_page``): solo los generan los enlaces y el
formulario de la propia aplicación, así que un valor manipulado no merece una página de error.

Servicios: ``current_app.extensions["invoice"]["entries"]`` (``EntryService``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from flask import (
    Blueprint,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

from app.formatting import parse_recent_page
from app.models import Entry, EntryFilter
from app.routes.images import MAX_ENTRY_ID
from app.services import (
    MISMATCH_FIELD,
    EntryNotFound,
    EntryService,
    SaveError,
    is_mismatch_confirmed,
)
from app.tax_id import is_valid as is_valid_tax_id
from app.validation import ENTRY_TYPES

bp = Blueprint("entries", __name__)

#: Query parameters of the listing filters, in the order used to build URLs.
FILTER_ARGS: tuple[str, ...] = ("type", "category", "date_from", "date_to")
#: Campos_Apunte of the edit form (same names as ``validation.validate_entry``).
FORM_FIELDS: tuple[str, ...] = (
    "invoice_date",
    "entry_type",
    "supplier",
    "tax_id",
    "invoice_number",
    "concept",
    "category",
    "base_amount",
    "vat_amount",
    "total",
)

SAVED_MESSAGE = "Apunte guardado correctamente."
SAVE_ERROR_MESSAGE = "No se ha podido guardar, inténtalo de nuevo."

# ``<input type="date">`` envía aaaa-mm-dd; [0-9] evita dígitos no ASCII y formatos
# alternativos que ``date.fromisoformat`` también admite.
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _service() -> EntryService:
    return current_app.extensions["invoice"]["entries"]


# --------------------------------------------------------------------------------------- #
# Filtros del listado
# --------------------------------------------------------------------------------------- #


def _parse_iso_date(raw: str) -> date | None:
    if not _ISO_DATE_RE.fullmatch(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:  # p. ej. 2024-02-30
        return None


def parse_filters(args: Mapping[str, str]) -> tuple[EntryFilter, dict[str, str]]:
    """Build the ``EntryFilter`` from the query string (Req. 8.3).

    Returns the filter and the applied filters as normalized strings (only valid, non-blank
    values), used to refill the filter form and to keep the filters in pagination links.
    Invalid values are ignored.
    """
    applied: dict[str, str] = {}

    raw_type = (args.get("type") or "").strip()
    entry_type = raw_type if raw_type in ENTRY_TYPES else None
    if entry_type is not None:
        applied["type"] = entry_type

    category = (args.get("category") or "").strip() or None
    if category is not None:
        applied["category"] = category

    dates: dict[str, date | None] = {}
    for name in ("date_from", "date_to"):
        parsed = _parse_iso_date((args.get(name) or "").strip())
        dates[name] = parsed
        if parsed is not None:
            applied[name] = parsed.isoformat()

    flt = EntryFilter(
        entry_type=entry_type,  # type: ignore[arg-type]  # comprobado contra ENTRY_TYPES
        category=category,
        date_from=dates["date_from"],
        date_to=dates["date_to"],
    )
    return flt, applied


def _wants_partial() -> bool:
    """HTMX request that swaps ``#entries``; a history restore needs the full page."""
    return bool(request.headers.get("HX-Request")) and not request.headers.get(
        "HX-History-Restore-Request"
    )


@bp.get("/entries")
def list_entries() -> Response:
    """Listado filtrado y paginado (Req. 8.1-8.4)."""
    flt, filters = parse_filters(request.args)
    page_number = parse_recent_page(request.args.get("page"))
    service = _service()
    page = service.list(flt, page_number)
    context = {
        "page": page,
        "filters": filters,
        "filtered": bool(filters),
        "totals": service.totals(flt) if filters else None,  # Req. 8.4
    }
    if _wants_partial():
        response = make_response(render_template("_entries_table.html", **context))
    else:
        response = make_response(
            render_template(
                "list.html",
                categories=service.category_names(),
                entry_types=sorted(ENTRY_TYPES),
                **context,
            )
        )
    # The same URL returns the page or the fragment depending on the header.
    response.vary.add("HX-Request")
    return response


# --------------------------------------------------------------------------------------- #
# Detalle, edición y borrado
# --------------------------------------------------------------------------------------- #


def _abort_if_out_of_range(entry_id: int) -> None:
    """Ids beyond SQLite INTEGER cannot exist (and would overflow the query): 404 (8.6)."""
    if entry_id > MAX_ENTRY_ID:
        abort(404)


def _get_or_404(entry_id: int) -> Entry:
    _abort_if_out_of_range(entry_id)
    entry = _service().get(entry_id)
    if entry is None:
        abort(404)
    return entry


def _amount_value(amount: Decimal | None) -> str:
    return "" if amount is None else format(amount, "f")


def entry_form_values(entry: Entry) -> dict[str, str]:
    """Form values of a stored Apunte (dates ISO, amounts like ``"1234.56"``)."""
    return {
        "invoice_date": entry.invoice_date.isoformat(),
        "entry_type": entry.entry_type,
        "supplier": entry.supplier or "",
        "tax_id": entry.tax_id or "",
        "invoice_number": entry.invoice_number or "",
        "concept": entry.concept or "",
        "category": entry.category or "",
        "base_amount": _amount_value(entry.base_amount),
        "vat_amount": _amount_value(entry.vat_amount),
        "total": _amount_value(entry.total),
    }


def _submitted_values(form: Mapping[str, str]) -> dict[str, str]:
    return {name: str(form.get(name, "")) for name in (*FORM_FIELDS, MISMATCH_FIELD)}


@bp.get("/entries/<int:entry_id>")
def detail(entry_id: int) -> str:
    """Detalle con todos los Campos_Apunte, el Texto_OCR y la imagen (Req. 6.6, 8.5, 8.6)."""
    entry = _get_or_404(entry_id)
    return render_template(
        "detail.html",
        entry=entry,
        saved=request.args.get("saved") == "1",
        saved_message=SAVED_MESSAGE,
        tax_id_warning=bool(entry.tax_id) and not is_valid_tax_id(entry.tax_id or ""),
    )


def _render_edit(
    entry: Entry,
    form: Mapping[str, str],
    *,
    errors: Mapping[str, str] | None = None,
    warnings: Mapping[str, str] | None = None,
    save_error: bool = False,
) -> str:
    return render_template(
        "edit.html",
        entry=entry,
        form=form,
        errors=errors or {},
        warnings=warnings or {},
        save_error_message=SAVE_ERROR_MESSAGE if save_error else None,
        categories=_service().category_names(),
        entry_types=sorted(ENTRY_TYPES),
        mismatch_field=MISMATCH_FIELD,
        mismatch_confirmed=is_mismatch_confirmed(form),
    )


@bp.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
def edit(entry_id: int) -> Response | str | tuple[str, int]:
    """Edición del Apunte con las validaciones del Requisito 5 (Req. 9.1).

    Válido → 303 a ``/entries/<id>?saved=1``; inválido o descuadre sin confirmar → 422 con
    los valores enviados; fallo al guardar → 500 con el formulario y el mensaje de error.
    """
    if request.method == "GET":
        entry = _get_or_404(entry_id)
        return _render_edit(entry, entry_form_values(entry))

    _abort_if_out_of_range(entry_id)
    form = request.form
    try:
        result = _service().update(entry_id, form, is_mismatch_confirmed(form))
    except EntryNotFound:
        abort(404)
    except SaveError:
        entry = _get_or_404(entry_id)
        return _render_edit(entry, _submitted_values(form), save_error=True), 500

    if result.saved:
        return redirect(url_for("entries.detail", entry_id=entry_id, saved=1), code=303)
    entry = _get_or_404(entry_id)
    values = {**_submitted_values(form), **result.form}
    return _render_edit(entry, values, errors=result.errors, warnings=result.warnings), 422


@bp.route("/entries/<int:entry_id>/delete", methods=["GET", "POST"])
def delete(entry_id: int) -> Response | str:
    """Confirmación explícita (GET, Req. 9.2) y borrado del Apunte y su imagen (POST, 9.3, 9.4)."""
    if request.method == "GET":
        return render_template("confirm_delete.html", entry=_get_or_404(entry_id))
    _abort_if_out_of_range(entry_id)
    try:
        _service().delete(entry_id)
    except EntryNotFound:
        abort(404)
    return redirect(url_for("entries.list_entries"), code=303)
