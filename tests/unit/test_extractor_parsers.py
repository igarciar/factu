"""Unit tests for the basic parsers of app.extractor (Req. 3.2, 3.4, 3.10)."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app import extractor
from app.extractor import AMOUNT_RE, DATE_RE, FIELDS, find_dates, parse_amount, parse_date


def test_regexes_are_precompiled_module_constants():
    assert isinstance(DATE_RE, re.Pattern)
    assert isinstance(AMOUNT_RE, re.Pattern)


def test_fields_lists_every_campo_apunte():
    assert FIELDS == (
        "invoice_date",
        "supplier",
        "tax_id",
        "invoice_number",
        "concept",
        "category",
        "entry_type",
        "base_amount",
        "vat_amount",
        "total",
    )


# --- parse_date --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("15/03/2024", date(2024, 3, 15)),
        ("15-03-2024", date(2024, 3, 15)),
        ("15.03.2024", date(2024, 3, 15)),
        ("15/03/24", date(2024, 3, 15)),
        ("5/3/2024", date(2024, 3, 5)),
        ("01/01/1900", date(1900, 1, 1)),
        ("31/12/99", date(2099, 12, 31)),
        ("29/02/2024", date(2024, 2, 29)),
        ("  15/03/2024  ", date(2024, 3, 15)),
    ],
)
def test_parse_date_accepts_supported_formats(token, expected):
    assert parse_date(token) == expected


@pytest.mark.parametrize(
    "token",
    [
        "15/03-2024",  # mixed separators
        "15.03/2024",
        "15-03.24",
        "31/02/2024",  # impossible date
        "29/02/2023",
        "00/01/2024",
        "15/13/2024",
        "01/01/0000",
        "15/03/202",  # three-digit year
        "2024-03-15",  # ISO is not an OCR format
        "Fecha: 15/03/2024",  # not a whole token
        "",
        "abc",
    ],
)
def test_parse_date_rejects_invalid_tokens(token):
    assert parse_date(token) is None


# --- find_dates --------------------------------------------------------------------------------


def test_find_dates_returns_valid_dates_in_order():
    text = "Fecha: 31/02/2024\nEmitida 15-03-2024, vence 15/04/24\nRef 01/02-2024 y 2024-05-01"
    assert find_dates(text) == [date(2024, 3, 15), date(2024, 4, 15)]


def test_find_dates_empty_text():
    assert find_dates("") == []
    assert find_dates("Sin fechas aquí 12345") == []


def test_find_dates_is_deterministic():
    text = "10.10.2010 y 11/11/11"
    assert find_dates(text) == find_dates(text) == [date(2010, 10, 10), date(2011, 11, 11)]


# --- parse_amount ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("1.234,56", "1234.56"),
        ("1.234.567,89", "1234567.89"),
        ("1234,56", "1234.56"),
        ("1234.56", "1234.56"),
        ("1,5", "1.50"),
        ("1.5", "1.50"),
        ("12", "12.00"),
        ("0", "0.00"),
        ("1.234", "1234.00"),  # no comma and not ".dd": dot is a thousands separator
        ("1,005", "1.01"),  # ROUND_HALF_UP
        ("1,004", "1.00"),
        ("1.234,56 €", "1234.56"),
        ("1.234,56€", "1234.56"),
        ("€ 12", "12.00"),
        ("€12,30", "12.30"),
        ("  99.99 €  ", "99.99"),
        ("1.234,56\u00a0€", "1234.56"),  # non-breaking space
    ],
)
def test_parse_amount_accepts_supported_formats(token, expected):
    result = parse_amount(token)
    assert result == Decimal(expected)
    assert result.as_tuple().exponent == -2


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "€",
        "abc",
        "12a",
        "-5",
        "1,234,56",
        "12.345.6",
        "1.2345",  # dot-decimal with more than two decimals is ambiguous
        "12,",
        ",50",
        "1 234,56",
        "21%",
    ],
)
def test_parse_amount_rejects_invalid_tokens(token):
    assert parse_amount(token) is None


def test_parse_amount_too_many_digits_returns_none():
    assert parse_amount("9" * 40) is None


def test_parse_amount_inverts_spanish_eur_format():
    # Shape produced by formatting.format_eur ("1.234,56 €").
    assert parse_amount("1.234.567,81 €") == Decimal("1234567.81")
    assert parse_amount("0,00 €") == Decimal("0.00")


def test_module_has_no_mutable_globals():
    public = {name for name in vars(extractor) if not name.startswith("_")}
    for name in public:
        assert not isinstance(getattr(extractor, name), (list, dict, set))
