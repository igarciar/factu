"""Pruebas unitarias de app.formatting (Requirements 16.6, 16.15, 16.18)."""

from decimal import Decimal

import pytest

from app.formatting import MAX_RECENT_PAGE, MONTH_NAMES_ES, format_eur, parse_recent_page


class TestFormatEur:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("0"), "0,00 €"),
            (0, "0,00 €"),
            (Decimal("999.99"), "999,99 €"),
            (Decimal("1000"), "1.000,00 €"),
            (Decimal("1234.5"), "1.234,50 €"),
            (Decimal("1234567.805"), "1.234.567,81 €"),
            (Decimal("0.005"), "0,01 €"),
            (Decimal("0.004"), "0,00 €"),
            (1234, "1.234,00 €"),
        ],
    )
    def test_spanish_format(self, value, expected):
        assert format_eur(value) == expected

    def test_none_returns_empty_string(self):
        assert format_eur(None) == ""

    def test_uses_regular_space_before_symbol(self):
        assert format_eur(Decimal("1")).endswith("\u0020€")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("-1234.5"), "-1.234,50 €"),
            (Decimal("-0.005"), "-0,01 €"),
            (-7, "-7,00 €"),
            # Un negativo que se redondea a cero no lleva signo.
            (Decimal("-0.004"), "0,00 €"),
        ],
    )
    def test_negative_values_have_leading_sign(self, value, expected):
        assert format_eur(value) == expected

    @pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
    def test_non_finite_raises(self, value):
        with pytest.raises(ValueError):
            format_eur(value)


class TestParseRecentPage:
    @pytest.mark.parametrize(
        "raw",
        [None, "", "abc", "0", "000", "-1", "+2", " 2", "2.0", "1e3", "٣", "²", "100001", "9" * 5000],
    )
    def test_invalid_returns_one(self, raw):
        assert parse_recent_page(raw) == 1

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1", 1), ("2", 2), ("007", 7), ("100000", MAX_RECENT_PAGE)],
    )
    def test_valid_values(self, raw, expected):
        assert parse_recent_page(raw) == expected

    def test_non_string_returns_one(self):
        assert parse_recent_page(5) == 1  # type: ignore[arg-type]


class TestMonthNames:
    def test_twelve_months_in_order(self):
        assert MONTH_NAMES_ES == (
            "Enero",
            "Febrero",
            "Marzo",
            "Abril",
            "Mayo",
            "Junio",
            "Julio",
            "Agosto",
            "Septiembre",
            "Octubre",
            "Noviembre",
            "Diciembre",
        )
        assert len(MONTH_NAMES_ES) == 12

    def test_max_recent_page(self):
        assert MAX_RECENT_PAGE == 100_000
