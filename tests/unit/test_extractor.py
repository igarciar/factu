"""Unit tests for the finders and ``extract`` of app.extractor (Req. 3.1, 3.3, 3.5-3.10)."""

from decimal import Decimal

import pytest

from app.extractor import (
    BASE_LABELS,
    FIELDS,
    TOTAL_LABELS,
    VAT_LABELS,
    extract,
    find_invoice_number,
    find_labeled_amount,
    find_supplier,
    find_tax_ids,
)
from app.models import Draft

SAMPLE_INVOICE = """
  ELECTRICIDAD DEL SUR, S.L.
CIF: B-1234567-4
C/ Mayor 1, 28001 Madrid
Factura nº: F-2024/0015
Vencimiento: 15/04/2024
Fecha: 15/03/2024
Concepto: suministro eléctrico
Subtotal 900,00 €
Base imponible 1.000,00 €
IVA 21% 210,00 €
Total 1.100,00 €
Total factura 1.210,00 €
"""


# --- find_tax_ids --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NIF 12345678Z", ["12345678Z"]),
        ("nif: 12 345 678-z", ["12345678Z"]),  # mixed separators, lower case
        ("NIE x-1234567-l", ["X1234567L"]),
        ("CIF B-1234567-4", ["B12345674"]),
        ("X1234567L y B12345674", ["X1234567L", "B12345674"]),  # order of appearance
        ("12345678Z y otra vez 12345678-Z", ["12345678Z"]),  # deduplicated
        ("Titular y 12345678 Z", ["12345678Z"]),  # rejected candidate does not hide the real one
        ("Emitida 01-02-2024 a las 10h", []),  # a date plus a one-letter word is not a NIF
        ("Tel 912345678, pedido 1234567", []),  # no pattern matches
        ("12345678\nZ", []),  # never across lines
        ("", []),
    ],
)
def test_find_tax_ids(text, expected):
    assert find_tax_ids(text) == expected


# --- find_labeled_amount -------------------------------------------------------------------------


def test_labeled_amount_subtotal_is_never_the_total():
    text = "Subtotal 900,00\nSub-total 901,00\nSub total 902,00\nTotal 1.089,00 €"
    assert find_labeled_amount(text, TOTAL_LABELS) == Decimal("1089.00")


def test_labeled_amount_only_subtotal_gives_no_total():
    assert find_labeled_amount("Subtotal 900,00", TOTAL_LABELS) is None


def test_labeled_amount_skips_vat_percentage():
    assert find_labeled_amount("IVA 21% 210,00 €", VAT_LABELS) == Decimal("210.00")
    assert find_labeled_amount("I.V.A. (21 %): 42.00", VAT_LABELS) == Decimal("42.00")


def test_labeled_amount_percentage_only_is_not_an_amount():
    assert find_labeled_amount("IVA 21%", VAT_LABELS) is None


def test_labeled_amount_prefers_more_specific_label_even_if_later():
    text = "Total 100,00\nImporte total 121,00"
    assert find_labeled_amount(text, TOTAL_LABELS) == Decimal("121.00")


def test_labeled_amount_ignores_case_and_accents():
    assert find_labeled_amount("CUOTA IVA: 5,25", VAT_LABELS) == Decimal("5.25")
    assert find_labeled_amount("Líquido 7,00", ["liquido"]) == Decimal("7.00")
    assert find_labeled_amount("LIQUIDO 8,00", ["líquido"]) == Decimal("8.00")


def test_labeled_amount_needs_amount_on_same_line():
    assert find_labeled_amount("Total\n50,00", TOTAL_LABELS) is None
    assert find_labeled_amount("Total\nTotal 20,00", TOTAL_LABELS) == Decimal("20.00")


def test_labeled_amount_skips_unparseable_number():
    text = "Total " + "9" * 40 + " 12,00"
    assert find_labeled_amount(text, TOTAL_LABELS) == Decimal("12.00")


def test_labeled_amount_label_must_be_whole_word():
    assert find_labeled_amount("Totales 10,00", TOTAL_LABELS) is None
    assert find_labeled_amount("Baseline 10,00", BASE_LABELS) is None


def test_labeled_amount_blank_labels_are_ignored():
    assert find_labeled_amount("Total 10,00", ["", "   "]) is None
    assert find_labeled_amount("Total 10,00", []) is None


# --- find_invoice_number -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Factura nº: F-2024/0015", "F-2024/0015"),
        ("FACTURA N.º 77", "77"),
        ("Nº Factura 123", "123"),
        ("Nº de factura: 2024-9", "2024-9"),
        ("Número de factura: a-77.", "A-77"),
        ("Invoice #INV-001", "INV-001"),
        ("Invoice No. 123", "123"),
        ("Invoice NO123", "NO123"),
        ("Sin número aquí", None),
        ("", None),
    ],
)
def test_find_invoice_number(text, expected):
    assert find_invoice_number(text) == expected


# --- find_supplier -------------------------------------------------------------------------------


def test_supplier_skips_empty_date_tax_id_and_amount_lines():
    text = "\n   \n15/03/2024\nB-1234567-4\n1.234,56 €\n  Taller Pérez  \nOtro"
    assert find_supplier(text) == "Taller Pérez"


@pytest.mark.parametrize("text", ["", "\n\n", "15/03/2024\n12345678Z\n€ 12"])
def test_supplier_none_without_meaningful_line(text):
    assert find_supplier(text) is None


# --- extract -------------------------------------------------------------------------------------


def test_extract_full_spanish_invoice():
    draft = extract(SAMPLE_INVOICE)
    assert draft.values == {
        "invoice_date": "2024-03-15",
        "supplier": "ELECTRICIDAD DEL SUR, S.L.",
        "tax_id": "B12345674",
        "invoice_number": "F-2024/0015",
        "concept": "",
        "category": "",
        "entry_type": "gasto",
        "base_amount": "1000.00",
        "vat_amount": "210.00",
        "total": "1210.00",
    }
    assert draft.missing == frozenset({"concept", "category"})
    assert draft.notice is None


def test_extract_empty_text_marks_everything_but_entry_type_missing():
    draft = extract("")
    assert draft.values == {field: "" for field in FIELDS} | {"entry_type": "gasto"}
    assert draft.missing == frozenset(FIELDS) - {"entry_type"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vencimiento 30/04/2024\nFecha 15/03/24", "2024-03-15"),  # after the "Fecha" label
        ("Emitida 01/02/2024\nFecha:", "2024-02-01"),  # label without date: first valid date
        ("Fecha 31/02/2024 01/03/2024", "2024-03-01"),  # impossible date skipped
        ("Pedido 31/02/2024", ""),  # only impossible date
        ("Fecha 15/03-2024", ""),  # mixed separators are not a date
        ("Fecha 15-03/2024 16.03.2024", "2024-03-16"),
        ("FECHA DE EMISIÓN: 05-06-2025", "2025-06-05"),
        ("Fechas 01/01/2024\nFecha 02/01/2024", "2024-01-02"),  # "Fechas" is not the label
    ],
)
def test_extract_invoice_date(text, expected):
    draft = extract(text)
    assert draft.values["invoice_date"] == expected
    assert ("invoice_date" in draft.missing) == (expected == "")


def test_extract_amounts_serialized_with_dot_and_two_decimals():
    draft = extract("Base 1.234,50\nIVA 21% 259.24\nTotal € 1494")
    assert draft.values["base_amount"] == "1234.50"
    assert draft.values["vat_amount"] == "259.24"
    assert draft.values["total"] == "1494.00"


def test_extract_uses_first_tax_id_of_the_text():
    draft = extract("Emisor 12345678Z\nCliente X1234567L")
    assert draft.values["tax_id"] == "12345678Z"


@pytest.mark.parametrize(
    "text",
    [
        "",
        SAMPLE_INVOICE,
        "Subtotal 5,00\nIVA 21%",
        "€€€ ### \t\n\r\n 31/02/2024",
        "Invoice #X\nTotal 1,00\nx" * 3,
    ],
)
def test_draft_invariant_and_determinism(text):
    draft = extract(text)
    assert isinstance(draft, Draft)
    assert set(draft.values) == set(FIELDS)
    assert draft.missing <= frozenset(FIELDS)
    assert draft.missing == frozenset(f for f in FIELDS if draft.values[f] == "")
    assert draft.values["entry_type"] == "gasto"
    assert "entry_type" not in draft.missing
    assert {"concept", "category"} <= draft.missing
    assert extract(text) == draft
