"""Rule-based field extractor: Texto_OCR -> Borrador (Req. 3).

Pure and deterministic (Req. 3.10): no global mutable state, no clock and no randomness. All
regular expressions are compiled once as module constants.

Layers:

- basic parsers: ``parse_date``, ``parse_amount``, ``find_dates``;
- finders: ``find_tax_ids``, ``find_labeled_amount``, ``find_invoice_number``, ``find_supplier``;
- ``extract(text) -> Draft``, which combines them into a Borrador.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from types import MappingProxyType

from app import tax_id
from app.models import Draft

FIELDS = (
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

CENTS = Decimal("0.01")

# Dates (Req. 3.2): dd/mm/aaaa, dd-mm-aaaa, dd.mm.aaaa and dd/mm/aa. The backreference ``(?P=sep)``
# enforces the design rule "the separator must be the same in both positions".
DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})(?P<sep>[/.\-])(?P<month>\d{1,2})(?P=sep)(?P<year>\d{4}|\d{2})\b"
)

# Amounts found inside free text (Req. 3.4), as written in the design. The finders use
# ``_LINE_AMOUNT_RE``: the same number alternatives with ASCII digits, boundaries so a number is
# never cut out of a longer one, and an optional ``%`` suffix to discard percentages.
AMOUNT_RE = re.compile(r"€?\s*(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2}|\d+)\s*€?")
_LINE_AMOUNT_RE = re.compile(
    r"(?<![0-9.,])"
    r"(?P<num>[0-9]{1,3}(?:\.[0-9]{3})+,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+\.[0-9]{2}|[0-9]+)"
    r"(?![0-9]|[.,][0-9])"
    r"(?P<pct>[ \t]*%)?"
)

# NIF/NIE/CIF candidates (Req. 3.3): 9 characters (letter or digit, 7 digits, control) with any
# run of spaces, tabs or hyphens between them, never across lines. The lookahead makes the scan
# overlapping, so a rejected candidate (e.g. ``"y 1234567 8"``) cannot hide the real identifier
# that starts one word later (``"12345678 Z"``).
_TAX_ID_SEP = r"[ \t\-]*"
TAX_ID_RE = re.compile(
    rf"(?=\b(?P<cand>[A-Za-z0-9](?:{_TAX_ID_SEP}[0-9]){{7}}{_TAX_ID_SEP}[A-Za-z0-9])\b)"
)
_TAX_ID_TRAILING_SEP = " \t-"

# Invoice number (Req. 3.6): design regex, kept on one line (``[ \t]`` instead of ``\s``) and in
# ASCII mode so ``[A-Z0-9]`` cannot match look-alikes such as ``ſ`` or the Kelvin sign. ``no`` must
# end the word, so ``"Invoice NO123"`` yields ``NO123`` and ``"Invoice No. 123"`` yields ``123``.
INVOICE_NUMBER_RE = re.compile(
    r"\b(?:factura[ \t]*n\.?[º°o]?\.?"
    r"|n\.?[º°o]?\.?[ \t]*(?:de[ \t]*)?factura"
    r"|n[úÚu]mero[ \t]+de[ \t]+factura"
    r"|invoice(?:[ \t]*(?:no\b\.?|n[º°]|\#))?)"
    r"[ \t]*[:#]?[ \t]*(?P<number>[A-Z0-9][A-Z0-9/\-.]{0,29})",
    re.IGNORECASE | re.ASCII,
)

# "Fecha" label (Req. 3.2): the invoice date is the first valid date after it.
DATE_LABEL_RE = re.compile(r"(?<!\w)fecha(?!\w)", re.IGNORECASE)

# Labels for base, VAT and total (Req. 3.5), already folded (lower case, no accents) and ordered
# from the most specific to the most generic.
BASE_LABELS = ("base imponible", "base")
VAT_LABELS = ("cuota iva", "iva", "i.v.a.")
TOTAL_LABELS = ("importe total", "total factura", "total a pagar", "total")

# A single amount token as accepted by ``parse_amount``: optional ``€`` before or after, optional
# spaces, and one of: Spanish format with thousands dots and optional decimal comma
# (``1.234,56``, ``1.234``), plain digits with optional decimal comma (``1234,56``, ``1,5``) or
# dot-decimal with one or two decimals (``1234.56``, ``1.5``).
_AMOUNT_TOKEN_RE = re.compile(
    r"\s*(?:€\s*)?"
    r"(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?|\d+\.\d{1,2})"
    r"(?:\s*€)?\s*"
)
_DOT_DECIMAL_RE = re.compile(r"\d+\.\d{1,2}")


def _date_from_match(match: re.Match[str]) -> date | None:
    """Build a date from a ``DATE_RE`` match; two-digit years mean ``2000 + aa``."""
    year_text = match["year"]
    year = int(year_text)
    if len(year_text) == 2:
        year += 2000
    try:
        return date(year, int(match["month"]), int(match["day"]))
    except ValueError:  # impossible calendar date such as 31/02 or year 0000
        return None


def parse_date(token: str) -> date | None:
    """Parse a whole token in ``dd/mm/aaaa``, ``dd-mm-aaaa``, ``dd.mm.aaaa`` or ``dd/mm/aa``.

    Returns ``None`` when the token is not exactly one date (after ``strip()``), when the two
    separators differ or when the date does not exist in the calendar.
    """
    match = DATE_RE.fullmatch(token.strip())
    if match is None:
        return None
    return _date_from_match(match)


def find_dates(text: str) -> list[date]:
    """Return every valid date in ``text``, in order of appearance (invalid ones are skipped)."""
    return [d for m in DATE_RE.finditer(text) if (d := _date_from_match(m)) is not None]


def parse_amount(token: str) -> Decimal | None:
    """Parse one amount token into a ``Decimal`` with two decimals (``ROUND_HALF_UP``).

    Rules (Req. 3.4): if there is a comma, dots are thousands separators and the comma is the
    decimal separator; without a comma, a trailing ``.d``/``.dd`` is a decimal point and any
    other dots are thousands separators. A ``€`` symbol before or after the number and spaces
    around it are allowed, so ``"1.234,56 €"``, ``"1234.56"`` and ``"€ 12"`` are all accepted.
    Returns ``None`` for anything else.
    """
    match = _AMOUNT_TOKEN_RE.fullmatch(token)
    if match is None:
        return None
    number = match["num"]
    if "," in number:
        normalized = number.replace(".", "").replace(",", ".")
    elif _DOT_DECIMAL_RE.fullmatch(number):
        normalized = number
    else:
        normalized = number.replace(".", "")
    try:
        return Decimal(normalized).quantize(CENTS, rounding=ROUND_HALF_UP)
    except InvalidOperation:  # more digits than the decimal context can quantize
        return None


# --- finders ------------------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Lower-case ``text`` and drop accents (NFKD + combining marks) to compare labels."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _label_regex(label: str) -> re.Pattern[str] | None:
    """Regex for a folded label as a whole word; never preceded by ``sub`` (``subtotal``)."""
    words = _fold(label).split()
    if not words:
        return None
    body = r"\s+".join(re.escape(word) for word in words)
    return re.compile(rf"(?<!\w)(?<!sub)(?<!sub )(?<!sub-){body}(?!\w)")


# Precompiled patterns of the default labels, exposed read-only (no mutable global state).
_LABEL_PATTERNS: Mapping[str, re.Pattern[str] | None] = MappingProxyType(
    {label: _label_regex(label) for label in (*BASE_LABELS, *VAT_LABELS, *TOTAL_LABELS)}
)


def _is_date_body(candidate: str) -> bool:
    """True when a tax id candidate is a full date plus one character (``01-02-2024 a``)."""
    body = candidate[:-1].rstrip(_TAX_ID_TRAILING_SEP)
    return parse_date(body) is not None


def find_tax_ids(text: str) -> list[str]:
    """Return the NIF/NIE/CIF in ``text``, normalized and in order of appearance (Req. 3.3).

    Candidates may contain spaces or hyphens between characters and lower-case letters. They are
    normalized with ``tax_id.normalize`` and kept only if they match the NIF, NIE or CIF pattern;
    the control character is not checked here (it is only a warning at validation, Req. 5.6).
    A date written with hyphens followed by a one-letter word is not taken as a NIF.
    """
    found: dict[str, None] = {}
    for match in TAX_ID_RE.finditer(text):
        candidate = match["cand"]
        normalized = tax_id.normalize(candidate)
        if tax_id.kind(normalized) is None or _is_date_body(candidate):
            continue
        found.setdefault(normalized, None)
    return list(found)


def find_labeled_amount(text: str, labels: Sequence[str]) -> Decimal | None:
    """Return the first amount written after one of ``labels`` on the same line (Req. 3.5).

    Labels are compared without case or accents and as whole words, so ``"total"`` never matches
    ``"subtotal"``/``"sub-total"``. They are tried in the given order (most specific first); for
    each label the lines are scanned top to bottom. Percentages such as ``21%`` are skipped.
    """
    lines = [_fold(line) for line in text.splitlines()]
    for label in labels:
        pattern = _LABEL_PATTERNS.get(label) or _label_regex(label)
        if pattern is None:
            continue
        for line in lines:
            amount = _amount_after_label(line, pattern)
            if amount is not None:
                return amount
    return None


def _amount_after_label(line: str, pattern: re.Pattern[str]) -> Decimal | None:
    label = pattern.search(line)
    if label is None:
        return None
    for match in _LINE_AMOUNT_RE.finditer(line, label.end()):
        if match["pct"]:
            continue
        amount = parse_amount(match["num"])
        if amount is not None:
            return amount
    return None


def find_invoice_number(text: str) -> str | None:
    """Return the invoice number after "Factura nº", "Nº factura", "Número de factura" or
    "Invoice" (Req. 3.6), in upper case and without a trailing full stop."""
    match = INVOICE_NUMBER_RE.search(text)
    if match is None:
        return None
    return match["number"].rstrip(".").upper()


def find_supplier(text: str) -> str | None:
    """Return the first stripped line that has a letter and is not a date, tax id or amount.

    Dates (``DATE_RE``) and amounts (``parse_amount``) contain no letters, so requiring a letter
    already rules them out (and empty lines); only whole-line NIF/NIE/CIF need a check.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not any(char.isalpha() for char in line):
            continue
        if tax_id.kind(tax_id.normalize(line)) is not None:
            continue
        return line
    return None


def _find_invoice_date(text: str) -> date | None:
    """First valid date after the first "Fecha" label, else the first valid date (Req. 3.2)."""
    label = DATE_LABEL_RE.search(text)
    if label is not None:
        after_label = find_dates(text[label.end():])
        if after_label:
            return after_label[0]
    dates = find_dates(text)
    return dates[0] if dates else None


def _format_amount(amount: Decimal | None) -> str:
    return "" if amount is None else format(amount, "f")


def extract(text: str) -> Draft:
    """Build a Borrador from a Texto_OCR (Req. 3.1-3.10).

    Every name in ``FIELDS`` is present in ``values``; undetected fields hold ``""`` and are
    listed in ``missing``. Dates are ISO (``aaaa-mm-dd``) and amounts ``"1234.56"``. Concept and
    category are never detected; ``entry_type`` is always ``"gasto"`` and never missing. The
    function is pure: same text, same Borrador.
    """
    invoice_date = _find_invoice_date(text)
    tax_ids = find_tax_ids(text)
    values = {
        "invoice_date": "" if invoice_date is None else invoice_date.isoformat(),
        "supplier": find_supplier(text) or "",
        "tax_id": tax_ids[0] if tax_ids else "",
        "invoice_number": find_invoice_number(text) or "",
        "concept": "",
        "category": "",
        "entry_type": "gasto",
        "base_amount": _format_amount(find_labeled_amount(text, BASE_LABELS)),
        "vat_amount": _format_amount(find_labeled_amount(text, VAT_LABELS)),
        "total": _format_amount(find_labeled_amount(text, TOTAL_LABELS)),
    }
    missing = frozenset(field for field in FIELDS if values[field] == "")
    return Draft(values=values, missing=missing)
