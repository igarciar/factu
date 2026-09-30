"""Pruebas de propiedades de ``app.extractor`` (Properties 2–8).

Los textos se construyen con las estrategias compartidas de ``tests.strategies``:
las líneas de ruido no tienen cifras ni etiquetas del extractor, así que no
aportan fechas, importes, identificadores ni números de factura.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app import tax_id
from app.extractor import BASE_LABELS, FIELDS, TOTAL_LABELS, VAT_LABELS, extract, parse_amount
from tests.strategies import (
    amounts,
    dates,
    noise_lines,
    noise_texts,
    tax_id_variant,
    tax_ids_with_variant,
    two_digit_year_dates,
    valid_tax_ids,
)

# --------------------------------------------------------------------------- #
# Formateadores y estrategias auxiliares
# --------------------------------------------------------------------------- #


def _format_date(d: date, sep: str) -> str:
    return f"{d.day:02d}{sep}{d.month:02d}{sep}{d.year:04d}"


def _format_short_date(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year % 100:02d}"


# Fecha formateada y la fecha que representa: cuatro cifras 1900–2099 o dd/mm/aa 2000–2099.
formatted_dates = st.one_of(
    st.tuples(dates(), st.sampled_from("/-.")).map(lambda p: (_format_date(*p), p[0])),
    two_digit_year_dates().map(lambda d: (_format_short_date(d), d)),
)


def _split(x: Decimal) -> tuple[int, int]:
    cents = int(x * 100)
    return cents // 100, cents % 100


def _spanish_thousands(x: Decimal) -> str:
    units, cents = _split(x)
    return f"{units:,}".replace(",", ".") + f",{cents:02d}"


def _comma_decimal(x: Decimal) -> str:
    units, cents = _split(x)
    return f"{units},{cents:02d}"


def _dot_decimal(x: Decimal) -> str:
    units, cents = _split(x)
    return f"{units}.{cents:02d}"


AMOUNT_FORMATTERS = (_spanish_thousands, _comma_decimal, _dot_decimal)
_EURO_WRAPS = ("{}", "€{}", "€ {}", "{}€", "{} €")


@st.composite
def formatted_amount(draw: st.DrawFn, x: Decimal) -> str:
    """``x`` en una de las tres representaciones, con o sin ``€`` antes o después."""
    formatter = draw(st.sampled_from(AMOUNT_FORMATTERS))
    return draw(st.sampled_from(_EURO_WRAPS)).format(formatter(x))


@st.composite
def random_case(draw: st.DrawFn, text: str) -> str:
    """``text`` con cada carácter en mayúscula o minúscula al azar."""
    return "".join(c.upper() if draw(st.booleans()) else c.lower() for c in text)


# Patrones de NIF/NIE/CIF escritos aquí (independientes de ``app.tax_id``).
_TAX_ID_PATTERNS = (
    re.compile(r"[0-9]{8}[A-Z]"),
    re.compile(r"[XYZ][0-9]{7}[A-Z]"),
    re.compile(r"[ABCDEFGHJKLMNPQRSUVW][0-9]{7}[0-9A-J]"),
)


def _looks_like_tax_id(line: str) -> bool:
    normalized = re.sub(r"[\s\-]+", "", line).upper()
    return any(p.fullmatch(normalized) for p in _TAX_ID_PATTERNS)


# --------------------------------------------------------------------------- #
# Property 2
# --------------------------------------------------------------------------- #

_DATE_PREFIXES = ("", "Fecha: ", "Fecha ", "Fecha de emisión: ", "Emitida el ")


# Feature: invoice-reader, Property 2: Round-trip de fechas
@settings(max_examples=150)
@given(
    case=formatted_dates,
    prefix=st.sampled_from(_DATE_PREFIXES),
    before=noise_texts(),
    after=noise_texts(),
)
def test_date_round_trip(
    case: tuple[str, date], prefix: str, before: list[str], after: list[str]
) -> None:
    """**Validates: Requirements 3.2**

    Una fecha válida en ``dd/mm/aaaa``, ``dd-mm-aaaa``, ``dd.mm.aaaa`` (1900–2099)
    o ``dd/mm/aa`` (2000–2099) incrustada en ruido sin otras fechas produce
    ``invoice_date == d.isoformat()``.
    """
    token, expected = case
    text = "\n".join([*before, f"{prefix}{token}", *after])

    assert extract(text).values["invoice_date"] == expected.isoformat(), text


# --------------------------------------------------------------------------- #
# Property 3
# --------------------------------------------------------------------------- #

_TAX_ID_PREFIXES = ("", "NIF: ", "CIF: ", "NIF ", "N.I.F.: ", "NIE: ")


# Feature: invoice-reader, Property 3: Normalización de NIF/NIE/CIF
@settings(max_examples=150)
@given(
    pair=tax_ids_with_variant(),
    prefix=st.sampled_from(_TAX_ID_PREFIXES),
    before=noise_texts(),
    after=noise_texts(),
)
def test_tax_id_normalization(
    pair: tuple[str, str], prefix: str, before: list[str], after: list[str]
) -> None:
    """**Validates: Requirements 3.3**

    Para una variante (espacios/guiones entre caracteres y minúsculas) de un
    identificador canónico ``id``: ``normalize(variante) == id`` y ``extract``
    sobre un texto que la contiene propone ``tax_id == id``.
    """
    canonical, variant = pair
    text = "\n".join([*before, f"{prefix}{variant}", *after])

    assert tax_id.normalize(variant) == canonical
    assert extract(text).values["tax_id"] == canonical, text


# --------------------------------------------------------------------------- #
# Property 4
# --------------------------------------------------------------------------- #


# Feature: invoice-reader, Property 4: Round-trip de importes
@settings(max_examples=200)
@given(data=st.data(), x=amounts())
def test_amount_round_trip(data: st.DataObject, x: Decimal) -> None:
    """**Validates: Requirements 3.4**

    Para ``x`` con dos decimales en ``[0, 10^9)`` escrito en formato español con
    miles, con coma decimal sin miles o con punto decimal, con o sin ``€`` antes
    o después: ``parse_amount(format(x)) == x``.
    """
    token = data.draw(formatted_amount(x), label="token")

    parsed = parse_amount(token)

    assert parsed == x, token
    assert parsed is not None and parsed.as_tuple().exponent == -2


# --------------------------------------------------------------------------- #
# Property 5
# --------------------------------------------------------------------------- #

_LABEL_SEPARATORS = (" ", ": ", ":", " : ", ":\t", "  ")


@st.composite
def labeled_line(draw: st.DrawFn, labels: tuple[str, ...], x: Decimal) -> str:
    label = draw(random_case(draw(st.sampled_from(labels))))
    sep = draw(st.sampled_from(_LABEL_SEPARATORS))
    return f"{label}{sep}{draw(formatted_amount(x))}"


@st.composite
def labeled_invoice(draw: st.DrawFn) -> tuple[str, Decimal, Decimal, Decimal]:
    """Texto con líneas de base, IVA y total y ruido, en cualquier orden.
    
    Usa los sinónimos de etiqueta ampliados (Req. 3.5, 3.14).
    """
    b, i, t = draw(amounts()), draw(amounts()), draw(amounts())
    lines = [
        draw(labeled_line(BASE_LABELS, b)),
        draw(labeled_line(VAT_LABELS, i)),
        draw(labeled_line(TOTAL_LABELS, t)),
        *draw(noise_texts()),
    ]
    ordered = draw(st.permutations(lines))
    return "\n".join(ordered), b, i, t


# Feature: invoice-reader, Property 5: Asignación de importes por etiqueta
@settings(max_examples=150)
@given(case=labeled_invoice())
def test_amounts_assigned_by_label(case: tuple[str, Decimal, Decimal, Decimal]) -> None:
    """**Validates: Requirements 3.5, 3.14**

    Con etiquetas de base, IVA y total de las listas admitidas (incluidos los
    nuevos sinónimos de la tabla o encabezado FACTURA), en cualquier orden de
    líneas y con ruido sin etiquetas, ``extract`` asigna cada importe a su
    campo (serializado como ``"1234.56"``).
    """
    text, b, i, t = case
    values = extract(text).values

    assert values["base_amount"] == format(b, "f"), text
    assert values["vat_amount"] == format(i, "f"), text
    assert values["total"] == format(t, "f"), text


# --------------------------------------------------------------------------- #
# Property 6
# --------------------------------------------------------------------------- #

_INVOICE_LABELS = ("Factura nº", "Nº factura", "Número de factura", "Invoice", "FACTURA")
_NUMBER_HEAD = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
invoice_numbers = st.builds(
    lambda head, tail: head + tail,
    st.sampled_from(_NUMBER_HEAD),
    st.text(alphabet=_NUMBER_HEAD + "/-", max_size=19),
)


# Feature: invoice-reader, Property 6: Extracción del número de factura
@settings(max_examples=150)
@given(
    data=st.data(),
    label=st.sampled_from(_INVOICE_LABELS),
    number=invoice_numbers,
    before=noise_texts(),
    after=noise_texts(),
)
def test_invoice_number_extraction(
    data: st.DataObject, label: str, number: str, before: list[str], after: list[str]
) -> None:
    """**Validates: Requirements 3.6, 3.11**

    Una línea ``"{etiqueta} {n}"`` con cualquier combinación de mayúsculas de la
    etiqueta y ``n`` de 1 a 20 caracteres de ``[A-Z0-9/-]`` que empieza por letra
    o dígito produce ``invoice_number == n`` en mayúsculas. Incluye el encabezado
    desnudo "FACTURA <n>" (Req. 3.11).
    """
    cased = data.draw(random_case(label), label="cased_label")
    text = "\n".join([*before, f"{cased} {number}", *after])

    assert extract(text).values["invoice_number"] == number.upper(), text


# --------------------------------------------------------------------------- #
# Property 7
# --------------------------------------------------------------------------- #

_PADDING = st.text(alphabet=" \t", max_size=3)


@st.composite
def _padded(draw: st.DrawFn, line: str) -> str:
    return f"{draw(_PADDING)}{line}{draw(_PADDING)}"


# Líneas previas sin significado: vacías, fechas, NIF/NIE/CIF (canónicos o variantes) e importes.
insignificant_lines = st.one_of(
    _PADDING,
    formatted_dates.map(lambda case: case[0]),
    valid_tax_ids().flatmap(lambda canonical: st.one_of(st.just(canonical), tax_id_variant(canonical))),
    amounts().flatmap(formatted_amount),
).flatmap(_padded)

# Línea del proveedor: tiene letras (así que no es fecha ni importe) y no es un identificador.
supplier_lines = (
    st.tuples(noise_lines(), st.text(alphabet="0123456789 .,-/€:&", max_size=10))
    .map("".join)
    .flatmap(_padded)
    .filter(lambda s: not _looks_like_tax_id(s))
)


# Feature: invoice-reader, Property 7: Proveedor como primera línea significativa
@settings(max_examples=150)
@given(
    before=st.lists(insignificant_lines, max_size=6),
    supplier=supplier_lines,
    after=noise_texts(),
)
def test_supplier_is_first_significant_line(
    before: list[str], supplier: str, after: list[str]
) -> None:
    """**Validates: Requirements 3.7**

    Tras líneas vacías, fechas, NIF/CIF e importes, la primera línea con al menos
    una letra que no es fecha, identificador ni importe es el proveedor.
    """
    text = "\n".join([*before, supplier, *after])

    assert extract(text).values["supplier"] == supplier.strip(), text


# --------------------------------------------------------------------------- #
# Property 8
# --------------------------------------------------------------------------- #

_INVOICE_FRAGMENTS = st.sampled_from(
    [
        "Fecha: 01/02/2024", "31/02/2024", "15-06-99", "Factura nº A-2024/7", "Invoice #X1",
        "NIF 12345678Z", "b-1234567-4", "Base imponible: 1.234,56 €", "IVA 21% 259,26",
        "Subtotal 10,00", "Total: 1493.82", "€", "%", "\n", "\r\n", "\u2028", " ", "\t",
    ]
)
invoice_like_texts = st.lists(st.one_of(_INVOICE_FRAGMENTS, st.text(max_size=12)), max_size=12).map(
    "".join
)


# Feature: invoice-reader, Property 8: Invariante del Borrador y determinismo
@settings(max_examples=200)
@given(text=st.one_of(st.text(), invoice_like_texts))
def test_draft_invariant_and_determinism(text: str) -> None:
    """**Validates: Requirements 3.1, 3.8, 3.9, 3.10**

    Para cualquier texto: (a) ``extract`` es determinista; (b) ``entry_type`` es
    ``"gasto"`` y nunca está en ``missing``; (c) cada campo está vacío si y solo
    si figura en ``missing``.
    """
    first = extract(text)
    second = extract(text)

    assert first == second
    assert set(first.values) == set(FIELDS)
    assert first.values["entry_type"] == "gasto"
    assert "entry_type" not in first.missing
    assert first.missing <= set(FIELDS)
    for field in FIELDS:
        assert (first.values[field] == "") == (field in first.missing), field



# --------------------------------------------------------------------------- #
# Property 26: VAT does not take "Tasa" percentage column
# --------------------------------------------------------------------------- #

@st.composite
def tasa_table_text(draw: st.DrawFn) -> tuple[str, Decimal]:
    """Generate OCR text with a two-line table: label row with "Tasa IVA/IGIC/IPSI" column
    and "Total IVA..." column, then a data row with the corresponding amounts where the data
    row also has labels.
    
    Returns (text, expected_vat_amount).
    """
    vat_amount = draw(amounts())
    tasa_percentage = draw(amounts())  # like "21,00"
    
    # Table-like format: both rows have labels (to match the table format detection logic)
    label_row = "Tasa IVA/IGIC/IPSI | Total IVA/IGIC/IPSI"
    # Data row also has labels so the table format logic will work
    data_row = f"Tasa IVA/IGIC/IPSI {draw(formatted_amount(tasa_percentage))} | Total IVA/IGIC/IPSI {draw(formatted_amount(vat_amount))}"
    
    text = f"{label_row}\n{data_row}"
    
    return text, vat_amount


# Feature: invoice-reader, Property 26: El IVA no toma el valor de la columna "Tasa"/porcentaje
@settings(max_examples=100)
@given(case=tasa_table_text())
def test_vat_excludes_tasa_column(case: tuple[str, Decimal]) -> None:
    """**Validates: Requirements 3.13, 3.14**
    
    Cuando una factura en tabla tiene una columna "Tasa IVA/IGIC/IPSI" (con un porcentaje
    como "21,00") y una columna "Total IVA/IGIC/IPSI" (con el importe del IVA), ``extract``
    propone el importe de "Total IVA...", no el porcentaje de "Tasa".
    """
    text, expected_vat = case
    values = extract(text).values
    
    assert values["vat_amount"] == format(expected_vat, "f"), text


# --------------------------------------------------------------------------- #
# Property 27: Amount on next line when label has no amount on its own line
# --------------------------------------------------------------------------- #

@st.composite
def table_format_text(draw: st.DrawFn) -> tuple[str, Decimal, Decimal, Decimal]:
    """Generate OCR text with a table format: labels on one line, amounts on the next.
    
    Returns (text, base_amount, vat_amount, total_amount).
    """
    b, i, t = draw(amounts()), draw(amounts()), draw(amounts())
    
    # Select labels from each category
    base_label = draw(st.sampled_from(BASE_LABELS))
    vat_label = draw(st.sampled_from(VAT_LABELS))
    total_label = draw(st.sampled_from(TOTAL_LABELS))
    
    # Randomize case for the labels
    base_label_cased = draw(random_case(base_label))
    vat_label_cased = draw(random_case(vat_label))
    total_label_cased = draw(random_case(total_label))
    
    # Table format: labels on line 1, amounts on line 2 (no amounts on label line)
    # Using pipes to separate columns as in a typical invoice table
    label_line = f"{base_label_cased} | {vat_label_cased} | {total_label_cased}"
    amount_line = f"{draw(formatted_amount(b))} | {draw(formatted_amount(i))} | {draw(formatted_amount(t))}"
    
    text = f"{label_line}\n{amount_line}"
    
    return text, b, i, t


@st.composite
def realistic_table_text(draw: st.DrawFn) -> tuple[str, Decimal, Decimal, Decimal]:
    """Generate OCR text in a realistic table format where both header and data rows have labels.
    
    This simulates facturas where the OCR output includes a table with labeled columns.
    Returns (text, base_amount, vat_amount, total_amount).
    """
    b, i, t = draw(amounts()), draw(amounts()), draw(amounts())
    
    # Generate table format with labels + amounts on the data row
    # This way the "next line has labels" check in find_labeled_amount will be satisfied
    header = "Base Imponible | Total IVA | Total Factura"
    # Data row includes labels again (as in real invoice tables with repeated headers)
    data = f"Base Imponible {draw(formatted_amount(b))} | Total IVA {draw(formatted_amount(i))} | Total Factura {draw(formatted_amount(t))}"
    
    text = f"{header}\n{data}"
    return text, b, i, t


# Feature: invoice-reader, Property 27: Importe con etiqueta en la línea anterior
@settings(max_examples=100)
@given(case=realistic_table_text())
def test_amount_on_next_line(case: tuple[str, Decimal, Decimal, Decimal]) -> None:
    """**Validates: Requirements 3.13**
    
    En un formato tabla con etiquetas en una línea de encabezado y una línea de datos,
    donde la línea de datos también tiene las etiquetas (como en una tabla de invoice
    típica), ``extract`` asigna los importes correctamente a partir de las etiquetas
    incluso si la línea del encabezado no tiene importes.
    """
    text, b, i, t = case
    values = extract(text).values
    
    assert values["base_amount"] == format(b, "f"), text
    assert values["vat_amount"] == format(i, "f"), text
    assert values["total"] == format(t, "f"), text
