"""Pruebas de ``app.security`` (cabeceras) y ``app.health`` (Endpoint_Salud sin Flask).

Requirements: 13.1, 13.2, 13.3, 14.5.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from flask import Flask

from app import health, security
from app.ocr import OcrStatus
from app.storage import ImageStore

OCR_OK = OcrStatus(available=True, languages=("spa", "eng"))
OCR_DOWN = OcrStatus(available=False, detail="Tesseract no encontrado")

EXPECTED_CSP = "default-src 'self'; img-src 'self'"


# -- security ----------------------------------------------------------------------------


@pytest.fixture
def secured_client():
    app = Flask(__name__)

    @app.get("/ok")
    def ok():
        return "hola"

    @app.get("/custom")
    def custom():
        return "x", 200, {"Content-Security-Policy": "default-src 'none'"}

    security.register(app)
    return app.test_client()


@pytest.mark.parametrize(("path", "code"), [("/ok", 200), ("/no-existe", 404)])
def test_security_headers_on_every_response(secured_client, path, code):
    response = secured_client.get(path)
    assert response.status_code == code
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == EXPECTED_CSP


def test_security_headers_do_not_override_route_values(secured_client):
    response = secured_client.get("/custom")
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_security_headers_constant_matches_design():
    assert dict(security.SECURITY_HEADERS) == {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": EXPECTED_CSP,
    }


# -- health ------------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "invoices.db"
    sqlite3.connect(path).close()
    return path


@pytest.fixture
def store(tmp_path: Path) -> ImageStore:
    return ImageStore(tmp_path / "images")


def test_health_ok(db_path, store):
    start = time.monotonic()
    body, code = health.check_health(db_path, store.tmp, OCR_OK)
    assert time.monotonic() - start < 2
    assert code == 200
    assert body == {
        "status": "ok",
        "database": {"ok": True},
        "images": {"ok": True, "writable": True},
        "ocr": {"ok": True, "languages": ["spa", "eng"]},
    }
    assert list(store.tmp.iterdir()) == []  # el fichero de sondeo se borra


def test_health_degraded_when_ocr_unavailable(db_path, store):
    body, code = health.check_health(db_path, store.tmp, OCR_DOWN)
    assert code == 200
    assert body["status"] == "degraded"
    assert body["ocr"] == {"ok": False, "languages": []}


def test_health_error_when_db_path_is_directory(tmp_path, store):
    body, code = health.check_health(tmp_path, store.tmp, OCR_OK)
    assert code == 503
    assert body["status"] == "error"
    assert body["database"] == {"ok": False}
    assert body["images"]["ok"] is True


def test_health_error_when_db_missing_does_not_create_it(tmp_path, store):
    missing = tmp_path / "nope" / "invoices.db"
    body, code = health.check_health(missing, store.tmp, OCR_OK)
    assert (code, body["database"]["ok"]) == (503, False)
    assert not missing.exists()


def test_health_error_when_tmp_missing(db_path, tmp_path):
    body, code = health.check_health(db_path, tmp_path / "no-such-tmp", OCR_OK)
    assert code == 503
    assert body["status"] == "error"
    assert body["images"] == {"ok": False, "writable": False}


def test_health_error_when_tmp_not_writable(db_path, store, monkeypatch):
    def deny(*_args, **_kwargs):
        raise PermissionError("solo lectura")

    monkeypatch.setattr("builtins.open", deny)
    body, code = health.check_health(db_path, store.tmp, OCR_DOWN)
    assert code == 503
    assert body["status"] == "error"
    assert body["images"] == {"ok": False, "writable": False}


def test_check_images_reports_failure_when_probe_cannot_be_deleted(store, monkeypatch):
    def deny(self, *_args, **_kwargs):
        raise PermissionError("bloqueado")

    monkeypatch.setattr(Path, "unlink", deny)
    assert health.check_images(store.tmp) == {"ok": False, "writable": False}


def test_check_database_never_raises_on_invalid_path():
    assert health.check_database("bad\0path.db") == {"ok": False}


@pytest.mark.parametrize(
    ("db_ok", "images_ok", "ocr_ok", "expected"),
    [
        (True, True, True, ("ok", 200)),
        (True, True, False, ("degraded", 200)),
        (False, True, True, ("error", 503)),
        (True, False, True, ("error", 503)),
        (False, False, False, ("error", 503)),
    ],
)
def test_overall_status(db_ok, images_ok, ocr_ok, expected):
    assert health.overall_status(db_ok, images_ok, ocr_ok) == expected
