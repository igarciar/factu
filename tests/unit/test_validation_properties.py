"""Pruebas de propiedades de app.validation (Properties 11, 12 y 13 del diseño).

Los valores esperados se calculan por construcción (oráculo del generador), sin usar
``parse_amount`` ni ``app.tax_id``, para no heredar posibles errores del código probado.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.tax_id import is_valid
from app.validation import (
    MSG_INVALID_AMOUNT,
    MSG_INVALID_DATE,
    MSG_INVALID_TAX_ID,
    MSG_INVALID_TYPE,
    MSG_NEGATIVE_AMOUNT,
    MSG_REQUIRED,
    validate_entry,
)
from tests.strategies import (
    CIF_DIGIT_ONLY,
    CIF_LETTER_ONLY,
    amounts,
    cents_to_decimal,
    dates,
    oracle_cif_digit,
    oracle_nif_letter,
    tax_id_variant,
    valid_tax_ids,
)

TOLERANCE = Decimal("0.01")
_ABSENT = object()  # la clave no aparece en el formulario
_PADDING = st.sampled_from(["", " ", "  ", "\t"])


@dataclass(frozen=True)
class FieldCase:
    """Valor crudo de un campo y lo que se espera de ``validate_entry``."""

    raw: object  # str o _ABSENT
    value: object = None  # valor limpio esperado si es válido
    error: str | None = None  # mensaje de error esperado, None si no hay error


def _absent_cases() -> st.SearchStrategy[FieldCase]:
    """Campo no informado: clave ausente, vacío o solo espacios."""
    return st.sampled_from([_ABSENT, "", "   ", "\t "]).map(FieldCase)


@st.composite
def _padded(draw: st.DrawFn, text: str) -> str:
    return draw(_PADDING) + text + draw(_PADDING)


# --------------------------------------------------------------------------- #
# Fecha
# --------------------------------------------------------------------------- #

_BAD_DATE_TEXTS = (
    "15/03/2024", "15-03-2024", "20240315", "2024-W05-3", "2024-1-5", "hoy", "2024/03/15",
    "2024-13-01", "2024-00-10", "2024-03-00", "24-03-15",
)


@st.composite
def _impossible_iso_dates(draw: st.DrawFn) -> str:
    """aaaa-mm-dd con un día que no existe en ese mes (p. ej. 2023-02-29)."""
    year = draw(st.integers(min_value=1900, max_value=2099))
    month = draw(st.integers(min_value=1, max_value=12))
    last_day = calendar.monthrange(year, month)[1]
    day = draw(st.integers(min_value=last_day + 1, max_value=99))
    return f"{year:04d}-{month:02d}-{day:02d}"


@st.composite
def date_cases(draw: st.DrawFn) -> FieldCase:
    kind = draw(st.sampled_from(["valid", "invalid", "absent"]))
    if kind == "valid":
        d = draw(dates())
        return FieldCase(draw(_padded(d.isoformat())), value=d)
    if kind == "invalid":
        text = draw(st.one_of(st.sampled_from(_BAD_DATE_TEXTS), _impossible_iso_dates()))
        return FieldCase(draw(_padded(text)), error=MSG_INVALID_DATE)
    return FieldCase(draw(_absent_cases()).raw, error=MSG_REQUIRED)


# --------------------------------------------------------------------------- #
# Tipo
# --------------------------------------------------------------------------- #


@st.composite
def type_cases(draw: st.DrawFn) -> FieldCase:
    kind = draw(st.sampled_from(["valid", "invalid", "absent"]))
    if kind == "valid":
        entry_type = draw(st.sampled_from(["gasto", "ingreso"]))
        return FieldCase(draw(_padded(entry_type)), value=entry_type)
    if kind == "invalid":
        text = draw(st.sampled_from(["otro", "gastos", "ingresos", "compra", "venta", "x", "1"]))
        return FieldCase(draw(_padded(text)), error=MSG_INVALID_TYPE)
    return FieldCase(draw(_absent_cases()).raw, error=MSG_REQUIRED)


# --------------------------------------------------------------------------- #
# Importes
# --------------------------------------------------------------------------- #


def _group_thousands(integer: str) -> str:
    groups = []
    while len(integer) > 3:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    groups.insert(0, integer)
    return ".".join(groups)


@st.composite
def formatted_amounts(draw: st.DrawFn, x: Decimal) -> str:
    """Representación textual de ``x`` en uno de los formatos admitidos (Req. 3.4)."""
    integer, decimals = f"{x:.2f}".split(".")
    style = draw(st.sampled_from(["es_thousands", "es", "dot", "integer"]))
    if style == "es_thousands":
        number = f"{_group_thousands(integer)},{decimals}"
    elif style == "es":
        number = f"{integer},{decimals}"
    elif style == "dot" or decimals != "00":
        number = f"{integer}.{decimals}"
    else:
        number = integer
    euro = draw(st.sampled_from(["none", "prefix", "suffix"]))
    if euro == "prefix":
        number = draw(st.sampled_from(["€", "€ "])) + number
    elif euro == "suffix":
        number += draw(st.sampled_from(["€", " €"]))
    return number


_BAD_AMOUNT_TEXTS = ("abc", "12a", "1,2,3", "1.2.3", "12..5", "€", "x€", "1.2.3,4.5", "doce")


@st.composite
def amount_cases(draw: st.DrawFn, *, required: bool) -> FieldCase:
    kind = draw(st.sampled_from(["valid", "valid", "negative", "invalid", "absent"]))
    if kind == "valid":
        x = draw(amounts())
        return FieldCase(draw(_padded(draw(formatted_amounts(x)))), value=x)
    if kind == "negative":
        magnitude = draw(amounts(min_cents=1))
        sign = draw(st.sampled_from(["-", "- "]))
        return FieldCase(sign + draw(formatted_amounts(magnitude)), error=MSG_NEGATIVE_AMOUNT)
    if kind == "invalid":
        return FieldCase(draw(_padded(draw(st.sampled_from(_BAD_AMOUNT_TEXTS)))),
                         error=MSG_INVALID_AMOUNT)
    return FieldCase(draw(_absent_cases()).raw, error=MSG_REQUIRED if required else None)


def _build_form(cases: dict[str, FieldCase]) -> dict[str, str]:
    return {name: case.raw for name, case in cases.items() if case.raw is not _ABSENT}


# --------------------------------------------------------------------------- #
# Property 11
# --------------------------------------------------------------------------- #


# Feature: invoice-reader, Property 11: Validación de campos obligatorios, fecha, tipo e importes
@settings(max_examples=200)
@given(
    date_case=date_cases(),
    type_case=type_cases(),
    base_case=amount_cases(required=False),
    vat_case=amount_cases(required=False),
    total_case=amount_cases(required=True),
    mismatch_confirmed=st.booleans(),
)
def test_required_date_type_and_amounts(
    date_case, type_case, base_case, vat_case, total_case, mismatch_confirmed
):
    """**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 9.1**"""
    cases = {
        "invoice_date": date_case,
        "entry_type": type_case,
        "base_amount": base_case,
        "vat_amount": vat_case,
        "total": total_case,
    }
    result = validate_entry(_build_form(cases), mismatch_confirmed=mismatch_confirmed)

    expected_errors = {name: case.error for name, case in cases.items() if case.error}
    assert result.errors == expected_errors

    b, i, t = base_case.value, vat_case.value, total_case.value
    mismatch = (
        not expected_errors
        and b is not None and i is not None and t is not None
        and abs(b + i - t) > TOLERANCE
    )
    should_save = not expected_errors and (not mismatch or mismatch_confirmed)
    assert (result.cleaned is not None) is should_save
    assert result.is_valid is should_save

    if should_save:
        assert result.cleaned.invoice_date == date_case.value
        assert result.cleaned.entry_type == type_case.value
        assert result.cleaned.total == t
        assert result.cleaned.base_amount == b
        assert result.cleaned.vat_amount == i


# --------------------------------------------------------------------------- #
# Property 12
# --------------------------------------------------------------------------- #


@st.composite
def amount_triples(draw: st.DrawFn) -> tuple[Decimal, Decimal, Decimal]:
    """(base, IVA, total), con total a menudo cerca de base + IVA para cubrir el umbral."""
    base = draw(amounts(max_cents=10**9))
    vat = draw(amounts(max_cents=10**9))
    if draw(st.booleans()):
        exact_cents = int((base + vat) * 100)
        delta = draw(st.integers(min_value=-3, max_value=3))
        total = cents_to_decimal(max(0, exact_cents + delta))
    else:
        total = draw(amounts())
    return base, vat, total


# Feature: invoice-reader, Property 12: Aviso de descuadre
@settings(max_examples=200)
@given(
    triple=amount_triples(),
    d=dates(),
    entry_type=st.sampled_from(["gasto", "ingreso"]),
    mismatch_confirmed=st.booleans(),
    data=st.data(),
)
def test_totals_mismatch_warning(triple, d, entry_type, mismatch_confirmed, data):
    """**Validates: Requirements 5.5**"""
    base, vat, total = triple
    form = {
        "invoice_date": d.isoformat(),
        "entry_type": entry_type,
        "base_amount": data.draw(formatted_amounts(base)),
        "vat_amount": data.draw(formatted_amounts(vat)),
        "total": data.draw(formatted_amounts(total)),
    }
    result = validate_entry(form, mismatch_confirmed=mismatch_confirmed)

    mismatch = abs(base + vat - total) > TOLERANCE
    assert result.errors == {}
    assert ("totals" in result.warnings) is mismatch
    assert result.is_valid is (not mismatch or mismatch_confirmed)


# --------------------------------------------------------------------------- #
# Property 13
# --------------------------------------------------------------------------- #

_CONTROL_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CIF_CONTROL_LETTERS = "JABCDEFGHI"
_NIE_DIGIT = {"X": "0", "Y": "1", "Z": "2"}


def _accepted_controls(canonical: str) -> set[str]:
    """Caracteres de control que el oráculo acepta para el cuerpo de ``canonical``."""
    first, body = canonical[0], canonical[1:8]
    if first.isdigit():
        return {oracle_nif_letter(int(canonical[:8]))}
    if first in _NIE_DIGIT:
        return {oracle_nif_letter(int(_NIE_DIGIT[first] + body))}
    digit = oracle_cif_digit(body)
    if first in CIF_LETTER_ONLY:
        return {_CIF_CONTROL_LETTERS[digit]}
    if first in CIF_DIGIT_ONLY:
        return {str(digit)}
    return {str(digit), _CIF_CONTROL_LETTERS[digit]}


@st.composite
def wrong_control_tax_ids(draw: st.DrawFn) -> str:
    """Identificador válido con el carácter de control sustituido por uno no aceptado.

    En los CIF que admiten dígito o letra se excluyen ambas formas, porque cambiar una por
    su equivalente sigue siendo un control correcto.
    """
    canonical = draw(valid_tax_ids())
    accepted = _accepted_controls(canonical)
    replacement = draw(st.sampled_from([c for c in _CONTROL_ALPHABET if c not in accepted]))
    return canonical[:8] + replacement


# Feature: invoice-reader, Property 13: Validación de NIF/CIF no bloqueante
@settings(max_examples=200)
@given(
    valid_id=valid_tax_ids(),
    wrong_id=wrong_control_tax_ids(),
    d=dates(),
    entry_type=st.sampled_from(["gasto", "ingreso"]),
    total=amounts(),
    data=st.data(),
)
def test_tax_id_validation_is_non_blocking(valid_id, wrong_id, d, entry_type, total, data):
    """**Validates: Requirements 5.6**"""
    assert is_valid(valid_id)
    assert not is_valid(wrong_id)

    base_form = {"invoice_date": d.isoformat(), "entry_type": entry_type, "total": f"{total:.2f}"}

    invalid_raw = data.draw(tax_id_variant(wrong_id), label="invalid_raw")
    result = validate_entry({**base_form, "tax_id": invalid_raw}, mismatch_confirmed=False)
    assert result.warnings.get("tax_id") == MSG_INVALID_TAX_ID
    assert "tax_id" not in result.errors
    assert result.cleaned is not None
    assert result.cleaned.tax_id == wrong_id

    valid_raw = data.draw(tax_id_variant(valid_id), label="valid_raw")
    result = validate_entry({**base_form, "tax_id": valid_raw}, mismatch_confirmed=False)
    assert "tax_id" not in result.warnings
    assert result.cleaned is not None
    assert result.cleaned.tax_id == valid_id
