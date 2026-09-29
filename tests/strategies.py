"""Estrategias Hypothesis compartidas (Req. 15.2, 15.4).

Los identificadores fiscales se generan con un oráculo propio que calcula el
carácter de control de forma independiente de ``app.tax_id``; así las pruebas
de propiedades no heredan un posible error del código que validan.
"""

from __future__ import annotations

import unicodedata
from datetime import date
from decimal import Decimal

from hypothesis import strategies as st

# --------------------------------------------------------------------------- #
# Fechas
# --------------------------------------------------------------------------- #

MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2099, 12, 31)


def dates(min_value: date = MIN_DATE, max_value: date = MAX_DATE) -> st.SearchStrategy[date]:
    """Fechas válidas del calendario entre 1900-01-01 y 2099-12-31 (por defecto)."""
    return st.dates(min_value=min_value, max_value=max_value)


def two_digit_year_dates() -> st.SearchStrategy[date]:
    """Fechas 2000–2099, las representables con año de dos cifras (``2000 + aa``)."""
    return dates(date(2000, 1, 1), MAX_DATE)


# --------------------------------------------------------------------------- #
# Importes
# --------------------------------------------------------------------------- #

MAX_AMOUNT_CENTS = 10**11 - 1  # 999_999_999.99 → intervalo [0, 10^9)
_CENT = Decimal("0.01")


def cents_to_decimal(cents: int) -> Decimal:
    """Convierte céntimos enteros en un ``Decimal`` con exponente ``-2`` exacto."""
    return (Decimal(cents) / 100).quantize(_CENT)


def amounts(min_cents: int = 0, max_cents: int = MAX_AMOUNT_CENTS) -> st.SearchStrategy[Decimal]:
    """``Decimal`` con dos decimales en ``[0, 10^9)`` (por defecto)."""
    return st.integers(min_value=min_cents, max_value=max_cents).map(cents_to_decimal)


# --------------------------------------------------------------------------- #
# NIF / NIE / CIF (oráculo independiente)
# --------------------------------------------------------------------------- #

_DNI_TABLE = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIE_DIGIT = {"X": 0, "Y": 1, "Z": 2}
_CIF_CONTROL_LETTERS = "JABCDEFGHI"
CIF_LETTER_ONLY = "PQRSNW"
CIF_DIGIT_ONLY = "ABEH"
CIF_EITHER = "CDFGJKLMUV"
CIF_FIRST_LETTERS = CIF_LETTER_ONLY + CIF_DIGIT_ONLY + CIF_EITHER

_seven_digits = st.integers(min_value=0, max_value=9_999_999)


def oracle_nif_letter(number: int) -> str:
    """Letra de control del DNI/NIF para un número de hasta 8 cifras."""
    return _DNI_TABLE[number % 23]


def oracle_cif_digit(body: str) -> int:
    """Dígito de control del CIF para sus 7 cifras centrales.

    Posiciones pares (2.ª, 4.ª, 6.ª): se suman tal cual. Posiciones impares
    (1.ª, 3.ª, 5.ª, 7.ª): se duplican y se suman las cifras del resultado.
    """
    digits = [int(c) for c in body]
    even_sum = sum(digits[1::2])
    odd_sum = sum(sum(divmod(d * 2, 10)) for d in digits[0::2])
    return (10 - (even_sum + odd_sum) % 10) % 10


@st.composite
def valid_nifs(draw: st.DrawFn) -> str:
    """NIF canónico válido: 8 cifras + letra de control."""
    number = draw(st.integers(min_value=0, max_value=99_999_999))
    return f"{number:08d}{oracle_nif_letter(number)}"


@st.composite
def valid_nies(draw: st.DrawFn) -> str:
    """NIE canónico válido: X/Y/Z + 7 cifras + letra de control."""
    prefix = draw(st.sampled_from("XYZ"))
    number = draw(_seven_digits)
    control = oracle_nif_letter(_NIE_DIGIT[prefix] * 10_000_000 + number)
    return f"{prefix}{number:07d}{control}"


@st.composite
def valid_cifs(draw: st.DrawFn) -> str:
    """CIF canónico válido con control dígito o letra según la letra inicial."""
    first = draw(st.sampled_from(CIF_FIRST_LETTERS))
    body = f"{draw(_seven_digits):07d}"
    digit = oracle_cif_digit(body)
    as_letter = _CIF_CONTROL_LETTERS[digit]
    if first in CIF_LETTER_ONLY:
        control = as_letter
    elif first in CIF_DIGIT_ONLY:
        control = str(digit)
    else:
        control = draw(st.sampled_from((str(digit), as_letter)))
    return f"{first}{body}{control}"


def valid_tax_ids() -> st.SearchStrategy[str]:
    """Cualquier NIF, NIE o CIF canónico válido."""
    return st.one_of(valid_nifs(), valid_nies(), valid_cifs())


_SEPARATORS = st.sampled_from(["", "", " ", "-", " - ", "  "])


@st.composite
def tax_id_variant(draw: st.DrawFn, canonical: str) -> str:
    """Variante de ``canonical`` con espacios/guiones entre caracteres y minúsculas.

    ``app.tax_id.normalize(variante) == canonical`` por construcción.
    """
    parts: list[str] = []
    for index, char in enumerate(canonical):
        if index:
            parts.append(draw(_SEPARATORS))
        parts.append(char.lower() if char.isalpha() and draw(st.booleans()) else char)
    return "".join(parts)


@st.composite
def tax_ids_with_variant(draw: st.DrawFn) -> tuple[str, str]:
    """Par ``(canónico, variante)`` de un identificador válido."""
    canonical = draw(valid_tax_ids())
    return canonical, draw(tax_id_variant(canonical))


# --------------------------------------------------------------------------- #
# Texto de ruido
# --------------------------------------------------------------------------- #

# Palabras que el extractor usa como etiqueta; el ruido no debe contenerlas.
LABEL_WORDS = (
    "fecha", "base", "imponible", "iva", "cuota", "total", "subtotal", "importe",
    "pagar", "factura", "invoice", "numero", "nif", "cif", "nie",
)

_NOISE_ALPHABET = "abcdefghijklmnopqrstuvwxyzáéíóúüñABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÜÑ"
_NOISE_VOCABULARY = (
    "Gracias", "por", "su", "compra", "Calle", "Mayor", "Madrid", "tienda",
    "cliente", "atención", "horario", "lunes", "viernes", "tarjeta", "efectivo",
    "Teléfono", "correo", "España", "devoluciones", "ticket", "caja",
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _is_noise_safe(text: str) -> bool:
    folded = _fold(text)
    return not any(word in folded for word in LABEL_WORDS)


def noise_words() -> st.SearchStrategy[str]:
    """Palabras solo con letras (sin cifras ni etiquetas del extractor)."""
    random_word = st.text(alphabet=_NOISE_ALPHABET, min_size=1, max_size=12)
    return st.one_of(st.sampled_from(_NOISE_VOCABULARY), random_word).filter(_is_noise_safe)


def noise_lines() -> st.SearchStrategy[str]:
    """Línea de ruido: sin cifras, así que sin fechas, importes ni NIF/CIF, y sin etiquetas."""
    return (
        st.lists(noise_words(), min_size=1, max_size=6)
        .map(" ".join)
        .filter(_is_noise_safe)  # la unión no puede formar una etiqueta, pero se comprueba igual
    )


def noise_texts(min_lines: int = 0, max_lines: int = 5) -> st.SearchStrategy[list[str]]:
    """Lista de líneas de ruido, lista para intercalar con líneas relevantes."""
    return st.lists(noise_lines(), min_size=min_lines, max_size=max_lines)


# --------------------------------------------------------------------------- #
# Imágenes mínimas
# --------------------------------------------------------------------------- #

JPEG_SIGNATURE = b"\xff\xd8\xff"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def jpeg_bytes(max_tail: int = 256) -> st.SearchStrategy[bytes]:
    """Bytes que empiezan por la firma JPEG seguida de contenido arbitrario."""
    return st.binary(max_size=max_tail).map(lambda tail: JPEG_SIGNATURE + tail)


def png_bytes(max_tail: int = 256) -> st.SearchStrategy[bytes]:
    """Bytes que empiezan por la firma PNG seguida de contenido arbitrario."""
    return st.binary(max_size=max_tail).map(lambda tail: PNG_SIGNATURE + tail)


def image_bytes(max_tail: int = 256) -> st.SearchStrategy[tuple[str, bytes]]:
    """Par ``(formato, bytes)`` con formato ``"jpg"`` o ``"png"``."""
    return st.one_of(
        jpeg_bytes(max_tail).map(lambda data: ("jpg", data)),
        png_bytes(max_tail).map(lambda data: ("png", data)),
    )
