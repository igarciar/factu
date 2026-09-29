"""Pruebas de propiedad de ``app/formatting.py``."""

from __future__ import annotations

import re
from decimal import Decimal

from hypothesis import given, settings

from app.extractor import parse_amount
from app.formatting import format_eur
from tests.strategies import amounts

_SPANISH_EUR_RE = re.compile(r"^\d{1,3}(\.\d{3})*,\d{2} €$")


# Feature: invoice-reader, Property 24: Round-trip del formato español de importes
@settings(max_examples=100)
@given(x=amounts())
def test_format_eur_round_trip(x: Decimal) -> None:
    """**Validates: Requirements 16.18**"""
    formatted = format_eur(x)

    assert _SPANISH_EUR_RE.fullmatch(formatted), formatted
    assert parse_amount(formatted) == x
