"""Fixtures compartidas de pytest (Req. 15.2, 15.4)."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings

from app.config import Settings
from tests.fakes import FakeOcrEngine

# Perfil por defecto: sin ``deadline`` porque el host Windows puede ser lento
# (E/S de ficheros, SQLite). Cada prueba fija ``max_examples`` con ``@settings``.
hypothesis_settings.register_profile(
    "invoice-reader",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
hypothesis_settings.register_profile("ci", parent=hypothesis_settings.get_profile("invoice-reader"), derandomize=True)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "invoice-reader"))


#: Instante fijo del Reloj de las pruebas (en el pasado: ningún Borrador recién creado caduca).
FIXED_NOW = datetime(2025, 6, 15, 10, 30, tzinfo=timezone.utc)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """``Settings`` con la BD y el almacén de imágenes dentro de ``tmp_path``."""
    return Settings(
        db_path=tmp_path / "db" / "invoices.db",
        images_dir=tmp_path / "images",
    )


@pytest.fixture
def fixed_clock() -> Callable[[], datetime]:
    """Reloj que siempre devuelve ``FIXED_NOW``."""
    return lambda: FIXED_NOW


@pytest.fixture
def fake_ocr() -> FakeOcrEngine:
    """Motor OCR sin Tesseract (texto vacío, estado disponible)."""
    return FakeOcrEngine()


@pytest.fixture
def app(settings: Settings, fake_ocr: FakeOcrEngine, fixed_clock: Callable[[], datetime]) -> Flask:
    """Aplicación completa de ``create_app`` sobre ``tmp_path`` (sin trabajo en segundo plano)."""
    # Importación diferida: las pruebas que no usan la aplicación no cargan los Blueprints.
    from app.main import create_app

    return create_app(settings, fake_ocr, clock=fixed_clock)


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Cliente de pruebas de Flask de la aplicación completa."""
    return app.test_client()
