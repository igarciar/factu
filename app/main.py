"""Application factory, start-up checks and local development server.

- ``create_app(settings, ocr_engine=None, clock=utc_now)`` builds the Flask application
  (*application factory*) and runs the synchronous start-up: writable directories (11.6,
  11.7), database schema (10.5), initial purge of expired Borradores (4.6) and the cached
  OCR status for ``/health`` (13.1).
- ``load_app(env)`` reads ``Settings`` from the environment and calls ``create_app``. On
  ``ConfigError``/``StartupError`` it logs the affected path and exits with code 1 (11.7).
  ``app/wsgi.py`` uses it for Gunicorn (``app.wsgi:app``, 10.6).
- ``run()`` starts Flask's development server on ``127.0.0.1``; local use only
  (``python -m app.main``), never inside the container.

Requirements: 1.5, 4.6, 10.5, 10.6, 11.5, 11.6, 11.7, 12.1, 12.2, 13.1, 14.7, 16.2, 16.18.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from flask import Flask, render_template, request
from werkzeug.exceptions import InternalServerError, NotFound, RequestEntityTooLarge

from app import repository, security
from app.config import ConfigError, Settings
from app.formatting import format_eur
from app.ocr import OcrEngine, TesseractOcrEngine
from app.routes import entries as entries_routes
from app.routes import health as health_routes
from app.routes import images as images_routes
from app.routes import pages as pages_routes
from app.services import DashboardService, DraftPurger, DraftService, EntryService, utc_now
from app.storage import TMP_DIRNAME, ImageStore

logger = logging.getLogger(__name__)

#: Extra bytes allowed over ``max_upload_bytes`` for multipart headers and form fields.
MULTIPART_OVERHEAD = 64 * 1024

#: Key of ``app.extensions`` holding the services used by the blueprints.
EXTENSION_KEY = "invoice"

_PROBE_PREFIX = ".startup-"


class StartupError(Exception):
    """A data directory cannot be created or written at start-up (Req. 11.7)."""

    def __init__(self, path: str | os.PathLike[str], reason: str | None = None) -> None:
        self.path = Path(path)
        self.reason = reason
        super().__init__(self.path_or_message)

    @property
    def path_or_message(self) -> str:
        """Affected path (plus the OS reason), logged by ``load_app``."""
        return str(self.path) if not self.reason else f"{self.path} ({self.reason})"


def _write_probe(directory: Path) -> None:
    """Create and delete a unique file in ``directory``; raises ``OSError`` on failure."""
    probe = directory / f"{_PROBE_PREFIX}{uuid4().hex}"
    with open(probe, "xb") as fh:
        fh.write(b"ok")
    probe.unlink()


def ensure_writable_dir(path: str | os.PathLike[str]) -> Path:
    """Create ``path`` if missing (11.6) and check it is writable; else ``StartupError`` (11.7)."""
    directory = Path(path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _write_probe(directory)
    except OSError as exc:
        raise StartupError(directory, exc.strerror or str(exc)) from exc
    return directory


def _startup(settings: Settings, purger: DraftPurger) -> None:
    """Initial purge of expired Borradores (4.6); a failure is logged and start-up goes on."""
    try:
        purged = purger.purge_now()
    except Exception:  # noqa: BLE001 - a failed purge must not stop the application
        logger.exception("Fallo en la purga inicial de borradores en %s", settings.images_dir)
        return
    if purged:
        logger.info("Purga inicial: %d ficheros de borradores caducados eliminados", len(purged))


def _init_schema(db_path: Path) -> None:
    try:
        repository.init_schema(db_path)
    except (sqlite3.Error, OSError) as exc:
        raise StartupError(db_path, str(exc)) from exc


def _register_error_handlers(app: Flask, settings: Settings) -> None:
    @app.errorhandler(NotFound)
    def not_found(_error: NotFound) -> tuple[str, int]:
        return render_template("error.html", status_code=404), 404

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error: RequestEntityTooLarge) -> tuple[str, int]:
        # Same message as FileTooLarge (Req. 1.5).
        message = pages_routes.too_large_message(settings.max_upload_bytes)
        return pages_routes.render_upload(message, 413)

    @app.errorhandler(InternalServerError)
    def internal_error(error: InternalServerError) -> tuple[str, int]:
        original = error.original_exception
        logger.exception(
            "Error no controlado en %s %s",
            request.method,
            request.path,
            exc_info=original if original is not None else True,
        )
        return render_template("error.html", status_code=500), 500


def create_app(
    settings: Settings,
    ocr_engine: OcrEngine | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> Flask:
    """Build the application and run the synchronous start-up.

    Raises ``StartupError`` if the database or image directories cannot be written, or the
    schema cannot be created.
    """
    ensure_writable_dir(settings.db_path.parent)
    ensure_writable_dir(settings.images_dir)
    ensure_writable_dir(settings.images_dir / TMP_DIRNAME)
    _init_schema(settings.db_path)

    engine: OcrEngine = (
        ocr_engine
        if ocr_engine is not None
        else TesseractOcrEngine(timeout_s=settings.ocr_timeout_seconds)
    )
    ttl = timedelta(hours=settings.draft_ttl_hours)
    store = ImageStore(settings.images_dir)

    def connect() -> sqlite3.Connection:
        return repository.connect(settings.db_path)

    entry_service = EntryService(store, connect, clock=clock, ttl=ttl)
    purger = DraftPurger(store, ttl=ttl, clock=clock)
    _startup(settings, purger)

    app = Flask(__name__, template_folder="templates", static_folder="static")
    # Jinja2 autoescape is Flask's default for .html templates (Req. 14.7).
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_bytes + MULTIPART_OVERHEAD
    app.add_template_filter(format_eur, "eur")
    security.register(app)

    app.extensions[EXTENSION_KEY] = {
        "settings": settings,
        "store": store,
        "drafts": DraftService(store, engine, clock=clock, ttl=ttl),
        "entries": entry_service,
        "dashboard": DashboardService(entry_service.entries, connect, clock=clock),
        "purger": purger,
        "ocr_status": engine.status(),
    }

    for blueprint in (pages_routes.bp, entries_routes.bp, images_routes.bp, health_routes.bp):
        app.register_blueprint(blueprint)
    _register_error_handlers(app, settings)
    return app


def _configure_logging() -> None:
    """Basic INFO logging to stdout unless the root logger already has handlers."""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            stream=sys.stdout,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


def load_app(env: Mapping[str, str] = os.environ) -> Flask:
    """``Settings.from_env(env)`` + ``create_app``; exits with code 1 if start-up fails."""
    _configure_logging()
    try:
        settings = Settings.from_env(env)
        return create_app(settings)
    except (ConfigError, StartupError) as exc:
        logger.error("No se puede arrancar: %s", exc.path_or_message)
        sys.exit(1)


def run() -> None:
    """Flask development server on ``127.0.0.1`` (local use only, also on Windows)."""
    app = load_app()
    settings: Settings = app.extensions[EXTENSION_KEY]["settings"]
    app.run(host="127.0.0.1", port=settings.port, debug=False)


if __name__ == "__main__":
    run()
