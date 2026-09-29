"""Pruebas de propiedades de ``app.formatting.parse_recent_page`` (Property 25)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.formatting import MAX_RECENT_PAGE, parse_recent_page

_ASCII_DIGITS = "0123456789"

# Entradas arbitrarias: Unicode libre, None, enteros (válidos o no) como texto y
# variantes con signo, espacios, exponentes o dígitos no ASCII.
arbitrary_raw = st.one_of(
    st.none(),
    st.text(),
    st.integers().map(str),
    st.text(alphabet=_ASCII_DIGITS, max_size=12),
    st.from_regex(r"\A[+\- ]?[0-9]{1,7}(e[0-9])?[ ]?\Z"),
    st.text(alphabet=st.characters(categories=["Nd"]), min_size=1, max_size=6),
)


def _is_ascii_digits(value: str) -> bool:
    return bool(value) and all(ch in _ASCII_DIGITS for ch in value)


# Feature: invoice-reader, Property 25: Normalización del número de Página_Recientes
@settings(max_examples=200)
@given(raw=arbitrary_raw)
def test_result_is_always_within_range(raw: str | None) -> None:
    result = parse_recent_page(raw)
    assert type(result) is int
    assert 1 <= result <= MAX_RECENT_PAGE


# Feature: invoice-reader, Property 25: Normalización del número de Página_Recientes
@settings(max_examples=200)
@given(n=st.integers(min_value=1, max_value=MAX_RECENT_PAGE))
def test_valid_page_round_trips(n: int) -> None:
    assert parse_recent_page(str(n)) == n


# Feature: invoice-reader, Property 25: Normalización del número de Página_Recientes
@settings(max_examples=200)
@given(n=st.one_of(st.integers(max_value=0), st.integers(min_value=MAX_RECENT_PAGE + 1)))
def test_out_of_range_integer_returns_one(n: int) -> None:
    assert parse_recent_page(str(n)) == 1


# Feature: invoice-reader, Property 25: Normalización del número de Página_Recientes
@settings(max_examples=200)
@given(raw=arbitrary_raw.filter(lambda s: s is None or not _is_ascii_digits(s)))
def test_non_ascii_digit_input_returns_one(raw: str | None) -> None:
    assert parse_recent_page(raw) == 1
