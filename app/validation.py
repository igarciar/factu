"""Validación pura del formulario de un Apunte (Requisitos 5.1-5.6 y 9.1).

``validate_entry`` recibe los valores del formulario tal como llegan (cadenas) y devuelve un
``ValidationResult`` con:

- ``cleaned``: el ``EntryInput`` listo para guardar, o ``None`` si hay errores o un descuadre
  sin confirmar.
- ``errors``: campo -> mensaje en español que bloquea el guardado.
- ``warnings``: avisos no bloqueantes (``"totals"`` y ``"tax_id"``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.extractor import parse_amount
from app.models import EntryInput, EntryType
from app.tax_id import is_valid, normalize

ENTRY_TYPES: frozenset[str] = frozenset({"gasto", "ingreso"})
MISMATCH_TOLERANCE = Decimal("0.01")
# Longitud máxima de los campos de texto libre, medida tras ``strip()``.
MAX_LENGTHS: dict[str, int] = {
    "supplier": 200,
    "concept": 200,
    "category": 60,
    "invoice_number": 40,
}

MSG_REQUIRED = "Este campo es obligatorio."
MSG_INVALID_DATE = "La fecha no es válida. Usa el formato aaaa-mm-dd."
MSG_INVALID_TYPE = "El tipo debe ser «gasto» o «ingreso»."
MSG_INVALID_AMOUNT = "No es un importe válido."
MSG_NEGATIVE_AMOUNT = "El importe debe ser mayor o igual que 0."
MSG_INVALID_TAX_ID = "El NIF/CIF no parece válido (formato o dígito de control). Puedes guardar igualmente."

# ``<input type="date">`` envía aaaa-mm-dd; [0-9] evita dígitos Unicode no ASCII y formatos
# alternativos que ``date.fromisoformat`` también admite (``20240131``, ``2024-W05-3``).
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# Signo menos delante del número, opcionalmente tras el símbolo del euro: "-5", "€ -5", "- 5 €".
_NEGATIVE_RE = re.compile(r"(?P<pre>€\s*)?-\s*(?P<rest>.*)", re.DOTALL)
_AMOUNT_FIELDS = ("base_amount", "vat_amount", "total")
_FORM_FIELDS = ("invoice_date", "entry_type", *_AMOUNT_FIELDS, "tax_id", *MAX_LENGTHS)


@dataclass
class ValidationResult:
    """Resultado de ``validate_entry``."""

    cleaned: EntryInput | None  # None si hay errores o un descuadre sin confirmar
    errors: dict[str, str] = field(default_factory=dict)  # campo -> mensaje (bloqueante)
    warnings: dict[str, str] = field(default_factory=dict)  # "tax_id", "totals" (no bloqueante)

    @property
    def is_valid(self) -> bool:
        return self.cleaned is not None


def validate_entry(form: Mapping[str, str], *, mismatch_confirmed: bool) -> ValidationResult:
    """Valida el formulario de un Apunte (alta o edición, Req. 9.1).

    Los campos ausentes o con solo espacios se tratan como no informados. Los opcionales no
    informados quedan a ``None`` en ``cleaned``; el NIF/CIF se guarda normalizado.
    """
    values = {key: _clean(form.get(key)) for key in _FORM_FIELDS}
    errors: dict[str, str] = {}
    warnings: dict[str, str] = {}

    invoice_date = _validate_date(values["invoice_date"], errors)
    entry_type = _validate_type(values["entry_type"], errors)
    amounts = {
        name: _validate_amount(name, values[name], errors, required=name == "total")
        for name in _AMOUNT_FIELDS
    }
    texts = {name: _validate_text(name, values[name], errors) for name in MAX_LENGTHS}
    tax_id = _validate_tax_id(values["tax_id"], warnings)

    base, vat, total = amounts["base_amount"], amounts["vat_amount"], amounts["total"]
    mismatch = False
    if base is not None and vat is not None and total is not None:
        difference = abs(base + vat - total)
        if difference > MISMATCH_TOLERANCE:
            mismatch = True
            warnings["totals"] = (
                f"La base imponible más el IVA ({base + vat:.2f} €) no coincide con el total "
                f"({total:.2f} €). Confirma para guardar con descuadre."
            )

    if errors or (mismatch and not mismatch_confirmed):
        return ValidationResult(cleaned=None, errors=errors, warnings=warnings)

    # Sin errores, los obligatorios están informados y son válidos.
    assert invoice_date is not None and entry_type is not None and total is not None
    cleaned = EntryInput(
        invoice_date=invoice_date,
        entry_type=entry_type,
        total=total,
        base_amount=base,
        vat_amount=vat,
        supplier=texts["supplier"],
        tax_id=tax_id,
        invoice_number=texts["invoice_number"],
        concept=texts["concept"],
        category=texts["category"],
    )
    return ValidationResult(cleaned=cleaned, errors=errors, warnings=warnings)


def _clean(raw: str | None) -> str | None:
    """Recorta espacios; devuelve ``None`` si el campo falta o queda vacío."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _validate_date(value: str | None, errors: dict[str, str]) -> date | None:
    if value is None:
        errors["invoice_date"] = MSG_REQUIRED
        return None
    if not _ISO_DATE_RE.fullmatch(value):
        errors["invoice_date"] = MSG_INVALID_DATE
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:  # p. ej. 2024-02-30
        errors["invoice_date"] = MSG_INVALID_DATE
        return None


def _validate_type(value: str | None, errors: dict[str, str]) -> EntryType | None:
    if value is None:
        errors["entry_type"] = MSG_REQUIRED
        return None
    if value not in ENTRY_TYPES:
        errors["entry_type"] = MSG_INVALID_TYPE
        return None
    return value  # type: ignore[return-value]  # comprobado contra ENTRY_TYPES


def _validate_amount(
    name: str, value: str | None, errors: dict[str, str], *, required: bool
) -> Decimal | None:
    if value is None:
        if required:
            errors[name] = MSG_REQUIRED
        return None
    negative = _NEGATIVE_RE.fullmatch(value)
    if negative is None:
        amount = parse_amount(value)
        if amount is None:
            errors[name] = MSG_INVALID_AMOUNT
        return amount
    magnitude = parse_amount((negative["pre"] or "") + negative["rest"])
    if magnitude is None:
        errors[name] = MSG_INVALID_AMOUNT
        return None
    if magnitude > 0:
        errors[name] = MSG_NEGATIVE_AMOUNT
        return None
    return magnitude  # "-0" equivale a 0,00


def _validate_text(name: str, value: str | None, errors: dict[str, str]) -> str | None:
    if value is None:
        return None
    limit = MAX_LENGTHS[name]
    if len(value) > limit:
        errors[name] = f"No puede superar {limit} caracteres (tiene {len(value)})."
        return None
    return value


def _validate_tax_id(value: str | None, warnings: dict[str, str]) -> str | None:
    if value is None:
        return None
    normalized = normalize(value)
    if not normalized:  # solo separadores, p. ej. "- -"
        return None
    if not is_valid(normalized):
        warnings["tax_id"] = MSG_INVALID_TAX_ID
    return normalized
