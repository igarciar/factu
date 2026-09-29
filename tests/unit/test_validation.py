"""Pruebas unitarias de app.validation (Requirements 5.1-5.6, 9.1)."""

from datetime import date
from decimal import Decimal

import pytest

from app.models import EntryInput
from app.validation import (
    MSG_INVALID_AMOUNT,
    MSG_INVALID_DATE,
    MSG_INVALID_TAX_ID,
    MSG_INVALID_TYPE,
    MSG_NEGATIVE_AMOUNT,
    MSG_REQUIRED,
    ValidationResult,
    validate_entry,
)


def _form(**overrides: str) -> dict[str, str]:
    """Formulario mínimo válido; ``overrides`` sustituye o añade campos."""
    form = {"invoice_date": "2024-03-15", "entry_type": "gasto", "total": "121.00"}
    form.update(overrides)
    return form


def _validate(form, *, confirmed: bool = False) -> ValidationResult:
    return validate_entry(form, mismatch_confirmed=confirmed)


class TestValidForm:
    def test_minimal_form_is_valid(self):
        result = _validate(_form())
        assert result.is_valid
        assert result.errors == {}
        assert result.warnings == {}
        assert result.cleaned == EntryInput(
            invoice_date=date(2024, 3, 15), entry_type="gasto", total=Decimal("121.00")
        )

    def test_full_form_is_cleaned(self):
        form = _form(
            entry_type="ingreso",
            base_amount="100",
            vat_amount="21,00",
            total="121.00 €",
            supplier="  Acme S.L.  ",
            tax_id=" 12345678-z ",
            invoice_number=" F-2024/001 ",
            concept=" Luz marzo ",
            category=" Suministros ",
        )
        result = _validate(form)
        assert result.errors == {}
        assert result.warnings == {}
        assert result.cleaned == EntryInput(
            invoice_date=date(2024, 3, 15),
            entry_type="ingreso",
            total=Decimal("121.00"),
            base_amount=Decimal("100.00"),
            vat_amount=Decimal("21.00"),
            supplier="Acme S.L.",
            tax_id="12345678Z",
            invoice_number="F-2024/001",
            concept="Luz marzo",
            category="Suministros",
        )

    def test_whitespace_only_optional_fields_become_none(self):
        form = _form(
            base_amount="  ",
            vat_amount="\t",
            supplier="   ",
            tax_id=" ",
            invoice_number="",
            concept="\n",
            category="  ",
        )
        result = _validate(form)
        assert result.errors == {}
        assert result.cleaned == EntryInput(
            invoice_date=date(2024, 3, 15), entry_type="gasto", total=Decimal("121.00")
        )

    def test_tax_id_with_only_separators_becomes_none(self):
        result = _validate(_form(tax_id="- -"))
        assert result.cleaned is not None
        assert result.cleaned.tax_id is None
        assert "tax_id" not in result.warnings

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.234,56 €", Decimal("1234.56")),
            ("1234,56", Decimal("1234.56")),
            ("1234.56", Decimal("1234.56")),
            ("€ 12", Decimal("12.00")),
            ("0", Decimal("0.00")),
            ("-0", Decimal("0.00")),
        ],
    )
    def test_amount_formats(self, raw, expected):
        result = _validate(_form(total=raw))
        assert result.errors == {}
        assert result.cleaned is not None
        assert result.cleaned.total == expected


class TestRequiredFields:
    def test_empty_form_reports_every_required_field(self):
        result = _validate({})
        assert result.cleaned is None
        assert result.errors == {
            "invoice_date": MSG_REQUIRED,
            "entry_type": MSG_REQUIRED,
            "total": MSG_REQUIRED,
        }

    @pytest.mark.parametrize("name", ["invoice_date", "entry_type", "total"])
    def test_whitespace_only_required_field_is_missing(self, name):
        result = _validate(_form(**{name: "   "}))
        assert result.cleaned is None
        assert result.errors == {name: MSG_REQUIRED}


class TestDate:
    @pytest.mark.parametrize(
        "raw", ["2024-02-30", "2023-02-29", "2024-13-01", "15/03/2024", "20240315", "2024-3-5", "hoy"]
    )
    def test_invalid_date(self, raw):
        result = _validate(_form(invoice_date=raw))
        assert result.cleaned is None
        assert result.errors == {"invoice_date": MSG_INVALID_DATE}

    def test_leap_day_is_valid(self):
        result = _validate(_form(invoice_date=" 2024-02-29 "))
        assert result.cleaned is not None
        assert result.cleaned.invoice_date == date(2024, 2, 29)


class TestEntryType:
    @pytest.mark.parametrize("raw", ["Gasto", "INGRESO", "otro", "gastos"])
    def test_invalid_type(self, raw):
        result = _validate(_form(entry_type=raw))
        assert result.cleaned is None
        assert result.errors == {"entry_type": MSG_INVALID_TYPE}

    def test_type_is_stripped(self):
        result = _validate(_form(entry_type=" ingreso "))
        assert result.cleaned is not None
        assert result.cleaned.entry_type == "ingreso"


class TestAmounts:
    @pytest.mark.parametrize("name", ["base_amount", "vat_amount", "total"])
    @pytest.mark.parametrize("raw", ["-5", "-1.234,56 €", "€ -5", "- 0,01"])
    def test_negative_amount(self, name, raw):
        result = _validate(_form(**{name: raw}))
        assert result.cleaned is None
        assert result.errors == {name: MSG_NEGATIVE_AMOUNT}

    @pytest.mark.parametrize("name", ["base_amount", "vat_amount", "total"])
    @pytest.mark.parametrize("raw", ["abc", "12,34,56", "1.2.3", "-abc", "--5", "12 €€"])
    def test_non_numeric_amount(self, name, raw):
        result = _validate(_form(**{name: raw}))
        assert result.cleaned is None
        assert result.errors == {name: MSG_INVALID_AMOUNT}

    def test_every_invalid_field_gets_its_own_error(self):
        form = _form(invoice_date="x", entry_type="y", total="-1", base_amount="z", vat_amount="-2")
        result = _validate(form)
        assert result.cleaned is None
        assert set(result.errors) == {"invoice_date", "entry_type", "total", "base_amount", "vat_amount"}


class TestMismatch:
    def test_difference_of_exactly_one_cent_has_no_warning(self):
        result = _validate(_form(base_amount="100,00", vat_amount="21,00", total="121,01"))
        assert "totals" not in result.warnings
        assert result.is_valid

    def test_difference_of_two_cents_warns_and_blocks(self):
        result = _validate(_form(base_amount="100,00", vat_amount="21,00", total="121,02"))
        assert result.cleaned is None
        assert result.errors == {}
        assert "totals" in result.warnings
        assert "121.00" in result.warnings["totals"]
        assert "121.02" in result.warnings["totals"]

    def test_confirmed_mismatch_is_valid(self):
        result = _validate(
            _form(base_amount="100", vat_amount="21", total="150"), confirmed=True
        )
        assert "totals" in result.warnings
        assert result.cleaned is not None
        assert result.cleaned.total == Decimal("150.00")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"base_amount": "100"},
            {"vat_amount": "21"},
            {"base_amount": "100", "vat_amount": "abc"},
        ],
    )
    def test_no_mismatch_check_when_base_or_vat_missing_or_invalid(self, overrides):
        result = _validate(_form(total="999", **overrides))
        assert "totals" not in result.warnings

    def test_mismatch_with_other_errors_still_warns(self):
        result = _validate(
            _form(base_amount="1", vat_amount="1", total="5", entry_type="x"), confirmed=True
        )
        assert result.cleaned is None
        assert "totals" in result.warnings
        assert result.errors == {"entry_type": MSG_INVALID_TYPE}


class TestMaxLengths:
    @pytest.mark.parametrize(
        ("name", "limit"),
        [("supplier", 200), ("concept", 200), ("category", 60), ("invoice_number", 40)],
    )
    def test_value_at_limit_is_accepted(self, name, limit):
        result = _validate(_form(**{name: "  " + "a" * limit + "  "}))
        assert result.cleaned is not None
        assert getattr(result.cleaned, name) == "a" * limit

    @pytest.mark.parametrize(
        ("name", "limit"),
        [("supplier", 200), ("concept", 200), ("category", 60), ("invoice_number", 40)],
    )
    def test_value_over_limit_is_rejected(self, name, limit):
        result = _validate(_form(**{name: "a" * (limit + 1)}))
        assert result.cleaned is None
        assert set(result.errors) == {name}
        assert str(limit) in result.errors[name]


class TestTaxId:
    @pytest.mark.parametrize("raw", ["12345678A", "B1234567X", "NOVALIDO"])
    def test_invalid_tax_id_warns_but_does_not_block(self, raw):
        result = _validate(_form(tax_id=raw))
        assert result.warnings == {"tax_id": MSG_INVALID_TAX_ID}
        assert "tax_id" not in result.errors
        assert result.cleaned is not None
        assert result.cleaned.tax_id == raw.upper()

    def test_valid_tax_id_is_normalized_without_warning(self):
        result = _validate(_form(tax_id="x-1234567-l"))
        assert result.warnings == {}
        assert result.cleaned is not None
        assert result.cleaned.tax_id == "X1234567L"
