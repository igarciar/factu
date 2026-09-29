"""Pruebas de arranque, manejadores globales, ``/health``, ``app.wsgi`` y ``gunicorn.conf.py``.

Requirements: 1.5, 4.6, 10.5, 10.6, 11.5, 11.6, 11.7, 12.1, 12.2, 13.1, 13.2, 13.3, 16.18.
"""

from __future__ import annotations

import importlib
import io
import logging
import os
import re
import runpy
import shutil
import sqlite3
import time
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from flask import Flask

from app import main
from app.config import Settings
from app.formatting import format_eur
from app.main import MULTIPART_OVERHEAD, StartupError, create_app, ensure_writable_dir, load_app
from app.ocr import OcrStatus, TesseractOcrEngine
from app.security import SECURITY_HEADERS
from app.storage import ImageStore
from tests.conftest import FIXED_NOW
from tests.fakes import FakeOcrEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "INVOICE_DB_PATH": str(tmp_path / "data" / "db" / "invoices.db"),
        "INVOICE_IMAGES_DIR": str(tmp_path / "data" / "images"),
        **extra,
    }


# -- Arranque ------------------------------------------------------------------------------


def test_create_app_creates_directories_and_schema(settings: Settings, fixed_clock) -> None:
    assert not settings.db_path.parent.exists()
    assert not settings.images_dir.exists()

    app = create_app(settings, FakeOcrEngine(), clock=fixed_clock)

    assert settings.db_path.parent.is_dir()
    assert (settings.images_dir / "tmp").is_dir()
    with sqlite3.connect(settings.db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entries", "categories"} <= tables
    # Sin restos del sondeo de escritura.
    assert not list(settings.db_path.parent.glob(".startup-*"))
    assert app.config["MAX_CONTENT_LENGTH"] == settings.max_upload_bytes + MULTIPART_OVERHEAD


def test_extensions_contract(app: Flask, settings: Settings, fake_ocr: FakeOcrEngine) -> None:
    services = app.extensions["invoice"]
    assert set(services) == {
        "settings", "store", "drafts", "entries", "dashboard", "purger", "ocr_status",
    }
    assert services["settings"] is settings
    assert isinstance(services["store"], ImageStore)
    assert services["drafts"].ocr is fake_ocr
    assert services["dashboard"].clock() == FIXED_NOW
    assert services["purger"].clock() == FIXED_NOW
    assert services["entries"].clock() == FIXED_NOW
    assert services["purger"].ttl == timedelta(hours=settings.draft_ttl_hours)
    assert services["ocr_status"] == fake_ocr.status()
    assert app.jinja_env.filters["eur"] is format_eur
    assert app.jinja_env.autoescape is not False


def test_default_ocr_engine_is_tesseract(settings: Settings) -> None:
    app = create_app(settings)
    engine = app.extensions["invoice"]["drafts"].ocr
    assert isinstance(engine, TesseractOcrEngine)
    assert engine.timeout_s == settings.ocr_timeout_seconds
    assert isinstance(app.extensions["invoice"]["ocr_status"], OcrStatus)


def test_initial_purge_removes_expired_drafts(settings: Settings, fixed_clock) -> None:
    tmp = settings.images_dir / "tmp"
    tmp.mkdir(parents=True)
    old = tmp / f"{'a' * 32}.png"
    fresh = tmp / f"{'b' * 32}.png"
    old.write_bytes(PNG_BYTES)
    fresh.write_bytes(PNG_BYTES)
    old_ts = (FIXED_NOW - timedelta(hours=48)).timestamp()
    os.utime(old, (old_ts, old_ts))
    fresh_ts = FIXED_NOW.timestamp()
    os.utime(fresh, (fresh_ts, fresh_ts))

    create_app(settings, FakeOcrEngine(), clock=fixed_clock)

    assert not old.exists()
    assert fresh.exists()


def test_initial_purge_failure_is_logged_and_startup_continues(
    settings: Settings, fixed_clock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(self, now, ttl):
        raise OSError("disco roto")

    monkeypatch.setattr(ImageStore, "purge_expired_drafts", boom)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        app = create_app(settings, FakeOcrEngine(), clock=fixed_clock)
    assert isinstance(app, Flask)
    assert "purga inicial" in caplog.text


def test_ensure_writable_dir_raises_startup_error_with_path(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("no soy un directorio")
    target = blocker / "db"
    with pytest.raises(StartupError) as excinfo:
        ensure_writable_dir(target)
    assert excinfo.value.path == target
    assert str(target) in excinfo.value.path_or_message


def test_load_app_exits_when_db_dir_cannot_be_created(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    env = _env(tmp_path, INVOICE_DB_PATH=str(blocker / "db" / "invoices.db"))
    with caplog.at_level(logging.ERROR, logger="app.main"), pytest.raises(SystemExit) as excinfo:
        load_app(env)
    assert excinfo.value.code == 1
    assert str(blocker / "db") in caplog.text


def test_load_app_exits_when_images_dir_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env = _env(tmp_path)
    images_dir = Path(env["INVOICE_IMAGES_DIR"])
    real_probe = main._write_probe

    def probe(directory: Path) -> None:
        # Simula permisos POSIX de solo lectura (no aplicables en Windows).
        if directory == images_dir:
            raise PermissionError(13, "Permission denied", str(directory))
        real_probe(directory)

    monkeypatch.setattr(main, "_write_probe", probe)
    with caplog.at_level(logging.ERROR, logger="app.main"), pytest.raises(SystemExit) as excinfo:
        load_app(env)
    assert excinfo.value.code == 1
    assert str(images_dir) in caplog.text
    assert "No se puede arrancar" in caplog.text


def test_load_app_exits_on_schema_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env = _env(tmp_path)

    def broken(db_path):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(main.repository, "init_schema", broken)
    with caplog.at_level(logging.ERROR, logger="app.main"), pytest.raises(SystemExit) as excinfo:
        load_app(env)
    assert excinfo.value.code == 1
    assert env["INVOICE_DB_PATH"] in caplog.text


def test_load_app_exits_on_config_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="app.main"), pytest.raises(SystemExit) as excinfo:
        load_app(_env(tmp_path, INVOICE_PORT="abc"))
    assert excinfo.value.code == 1
    assert "INVOICE_PORT" in caplog.text


def test_load_app_builds_app_from_env(tmp_path: Path) -> None:
    app = load_app(_env(tmp_path, INVOICE_MAX_UPLOAD_BYTES="2048"))
    settings = app.extensions["invoice"]["settings"]
    assert settings.db_path == tmp_path / "data" / "db" / "invoices.db"
    assert app.config["MAX_CONTENT_LENGTH"] == 2048 + MULTIPART_OVERHEAD
    assert (tmp_path / "data" / "images" / "tmp").is_dir()


def test_run_uses_localhost_and_configured_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in _env(tmp_path, INVOICE_PORT="8123").items():
        monkeypatch.setenv(name, value)
    calls: list[dict] = []
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: calls.append(kwargs))
    main.run()
    assert calls == [{"host": "127.0.0.1", "port": 8123, "debug": False}]


def test_recreated_app_keeps_entries_and_images(settings: Settings, fixed_clock) -> None:
    client = create_app(settings, FakeOcrEngine(), clock=fixed_clock).test_client()
    upload = client.post(
        "/uploads",
        data={"file": (io.BytesIO(PNG_BYTES), "factura.png")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    draft_id = re.search(r"/drafts/([0-9a-f]{32})/confirm", upload.get_data(as_text=True)).group(1)
    saved = client.post(
        f"/drafts/{draft_id}/confirm",
        data={"invoice_date": "2025-05-01", "entry_type": "gasto", "total": "12.10", "supplier": "Ferretería"},
    )
    assert saved.status_code == 303
    entry_path = saved.headers["Location"].split("?")[0]

    # "Recrear el contenedor": nueva aplicación con los mismos directorios.
    again = create_app(settings, FakeOcrEngine(), clock=fixed_clock).test_client()
    detail = again.get(entry_path)
    assert detail.status_code == 200
    assert "Ferretería" in detail.get_data(as_text=True)
    image = again.get(f"{entry_path}/image")
    assert image.status_code == 200
    assert image.data == PNG_BYTES


# -- /health -------------------------------------------------------------------------------


def test_health_ok(client) -> None:
    start = time.monotonic()
    response = client.get("/health")
    assert time.monotonic() - start < 2
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] and body["images"]["writable"] and body["ocr"]["ok"]


def test_health_degraded_when_ocr_down(settings: Settings, fixed_clock) -> None:
    down = FakeOcrEngine(status=OcrStatus(available=False, languages=()))
    response = create_app(settings, down, clock=fixed_clock).test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "degraded"


def test_health_503_when_database_missing(client, settings: Settings) -> None:
    settings.db_path.unlink()
    response = client.get("/health")
    assert response.status_code == 503
    body = response.get_json()
    assert body["status"] == "error" and body["database"]["ok"] is False


def test_health_503_when_store_not_writable(client, settings: Settings) -> None:
    shutil.rmtree(settings.images_dir / "tmp")
    response = client.get("/health")
    assert response.status_code == 503
    assert response.get_json()["images"]["writable"] is False


# -- Manejadores globales y cabeceras ------------------------------------------------------


def test_404_renders_error_page(client) -> None:
    response = client.get("/no-existe")
    assert response.status_code == 404
    assert "Página no encontrada" in response.get_data(as_text=True)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_413_body_larger_than_max_content_length(settings: Settings, fixed_clock) -> None:
    small = replace(settings, max_upload_bytes=1024)
    app = create_app(small, FakeOcrEngine(), clock=fixed_clock)
    body = b"x" * (app.config["MAX_CONTENT_LENGTH"] + 1)
    response = app.test_client().post(
        "/uploads",
        data={"file": (io.BytesIO(body), "grande.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 413
    text = response.get_data(as_text=True)
    assert "Tamaño máximo: 1 KB" in text
    assert 'action="/uploads"' in text


def test_500_renders_generic_error_and_logs(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    def failing() -> str:
        raise RuntimeError("detalle interno secreto")

    app.add_url_rule("/boom", view_func=failing)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        response = app.test_client().get("/boom")
    assert response.status_code == 500
    text = response.get_data(as_text=True)
    assert "Algo ha fallado" in text
    assert "detalle interno secreto" not in text
    assert "Traceback" not in text
    assert any(r.name == "app.main" and r.exc_info and r.exc_info[0] is RuntimeError for r in caplog.records)
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_security_headers_on_pages_and_health(client) -> None:
    for path in ("/upload", "/health"):
        response = client.get(path)
        assert response.status_code == 200
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value


def test_eur_filter_available_in_templates(app: Flask) -> None:
    with app.app_context():
        rendered = app.jinja_env.from_string("{{ value | eur }}").render(value=Decimal("1234.5"))
    assert rendered == format_eur(Decimal("1234.5"))


# -- WSGI y Gunicorn -----------------------------------------------------------------------


def test_wsgi_module_exposes_flask_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _env(tmp_path).items():
        monkeypatch.setenv(name, value)
    module = importlib.import_module("app.wsgi")
    module = importlib.reload(module)  # rearranca con las variables de este test

    assert isinstance(module.app, Flask)
    assert callable(module.app)
    assert module.app.extensions["invoice"]["settings"].db_path == Path(_env(tmp_path)["INVOICE_DB_PATH"])
    assert module.app.test_client().get("/health").status_code == 200


def test_gunicorn_conf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOICE_PORT", "8765")
    conf = runpy.run_path(str(REPO_ROOT / "gunicorn.conf.py"))
    assert conf["bind"] == "0.0.0.0:8765"
    assert conf["workers"] == 2
    assert conf["worker_class"] == "sync"
    assert conf["timeout"] > 60
    assert conf["graceful_timeout"] == 30
    assert conf["preload_app"] is True
    assert conf["accesslog"] == "-" and conf["errorlog"] == "-"
