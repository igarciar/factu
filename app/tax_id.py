"""Normalización y validación de identificadores fiscales españoles (NIF, NIE, CIF).

Funciones puras y deterministas:

- ``normalize``: quita espacios y guiones y pasa a mayúsculas.
- ``kind``: clasifica un valor ya normalizado como ``"NIF"``, ``"NIE"`` o ``"CIF"``.
- ``is_valid``: comprueba formato y dígito/letra de control de un valor normalizado.
"""

from __future__ import annotations

import re
from typing import Literal

TaxIdKind = Literal["NIF", "NIE", "CIF"]

# Se usa [0-9] en lugar de \d para no aceptar dígitos Unicode no ASCII.
_NIF_RE = re.compile(r"[0-9]{8}[A-Z]")
_NIE_RE = re.compile(r"[XYZ][0-9]{7}[A-Z]")
_CIF_RE = re.compile(r"[ABCDEFGHJKLMNPQRSUVW][0-9]{7}[0-9A-J]")
_SEPARATORS_RE = re.compile(r"[\s\-]+")

_NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIE_PREFIX = {"X": "0", "Y": "1", "Z": "2"}
_CIF_LETTERS = "JABCDEFGHI"
# Letra inicial del CIF -> tipo de carácter de control admitido.
_CIF_LETTER_CONTROL = frozenset("PQRSNW")
_CIF_DIGIT_CONTROL = frozenset("ABEH")


def normalize(raw: str) -> str:
    """Devuelve ``raw`` sin espacios ni guiones y en mayúsculas."""
    return _SEPARATORS_RE.sub("", raw).upper()


def kind(value: str) -> TaxIdKind | None:
    """Clasifica un identificador normalizado por su formato; ``None`` si no encaja."""
    if _NIF_RE.fullmatch(value):
        return "NIF"
    if _NIE_RE.fullmatch(value):
        return "NIE"
    if _CIF_RE.fullmatch(value):
        return "CIF"
    return None


def is_valid(value: str) -> bool:
    """Indica si ``value`` (normalizado) tiene formato correcto y control válido."""
    match kind(value):
        case "NIF":
            return _nif_letter(value[:8]) == value[8]
        case "NIE":
            return _nif_letter(_NIE_PREFIX[value[0]] + value[1:8]) == value[8]
        case "CIF":
            return _cif_control_ok(value)
        case _:
            return False


def _nif_letter(digits: str) -> str:
    return _NIF_LETTERS[int(digits) % 23]


def _cif_control_digit(digits: str) -> int:
    """Dígito de control estándar del CIF sobre sus 7 dígitos centrales."""
    total = 0
    for position, char in enumerate(digits, start=1):
        n = int(char)
        if position % 2 == 0:
            total += n
        else:
            doubled = n * 2
            total += doubled // 10 + doubled % 10
    return (10 - total % 10) % 10


def _cif_control_ok(value: str) -> bool:
    first, control = value[0], value[8]
    expected_digit = _cif_control_digit(value[1:8])
    digit_ok = control == str(expected_digit)
    letter_ok = control == _CIF_LETTERS[expected_digit]
    if first in _CIF_LETTER_CONTROL:
        return letter_ok
    if first in _CIF_DIGIT_CONTROL:
        return digit_ok
    return digit_ok or letter_ok
