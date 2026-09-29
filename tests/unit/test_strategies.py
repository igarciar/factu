"""Comprobaciones de cordura de ``tests/strategies.py`` y ``tests/conftest.py``."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import settings as hsettings

from app import tax_id, uploads
from app.config import Settings
from tests import strategies as s


def test_settings_fixture_points_to_tmp_path(settings: Settings, tmp_path):
    assert settings.db_path.is_relative_to(tmp_path)
    assert settings.images_dir.is_relative_to(tmp_path)
    assert settings.max_upload_bytes == Settings().max_upload_bytes


def test_oracle_known_examples():
    assert s.oracle_nif_letter(12345678) == "Z"
    assert s.oracle_nif_letter(1234567) == "L"  # NIE X1234567L
    assert s.oracle_cif_digit("1234567") == 4  # B12345674 / P1234567D


def test_signatures_match_app():
    assert s.JPEG_SIGNATURE == uploads.JPEG_MAGIC
    assert s.PNG_SIGNATURE == uploads.PNG_MAGIC


@hsettings(max_examples=200)
@given(s.valid_tax_ids())
def test_oracle_ids_accepted_by_app(value: str):
    assert tax_id.kind(value) is not None
    assert tax_id.is_valid(value)


@hsettings(max_examples=100)
@given(s.tax_ids_with_variant())
def test_variants_normalize_to_canonical(pair: tuple[str, str]):
    canonical, variant = pair
    assert tax_id.normalize(variant) == canonical


@hsettings(max_examples=100)
@given(s.dates(), s.two_digit_year_dates())
def test_dates_in_range(d, d2):
    assert s.MIN_DATE <= d <= s.MAX_DATE
    assert 2000 <= d2.year <= 2099


@hsettings(max_examples=100)
@given(s.amounts())
def test_amounts_two_decimals_in_range(x: Decimal):
    assert Decimal(0) <= x < Decimal(10**9)
    assert x.as_tuple().exponent == -2


@hsettings(max_examples=100)
@given(s.noise_lines())
def test_noise_lines_have_no_digits_or_labels(line: str):
    assert line.strip()
    assert not any(c.isdigit() for c in line)
    assert s._is_noise_safe(line)


@hsettings(max_examples=100)
@given(s.image_bytes())
def test_image_bytes_detected_by_signature(pair: tuple[str, bytes]):
    fmt, data = pair
    assert uploads.detect_format(data) == fmt
