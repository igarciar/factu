"""Application settings read from environment variables.

Requirements: 1.3, 11.1, 11.2, 11.3, 12.1, 12.2.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_DB_PATH = "INVOICE_DB_PATH"
ENV_IMAGES_DIR = "INVOICE_IMAGES_DIR"
ENV_MAX_UPLOAD_BYTES = "INVOICE_MAX_UPLOAD_BYTES"
ENV_PORT = "INVOICE_PORT"


class ConfigError(Exception):
    """Invalid configuration value; the process must exit with a non-zero code."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    @property
    def path_or_message(self) -> str:
        """Uniform attribute logged by ``load_app`` for ConfigError and StartupError."""
        return self.message


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("/data/db/invoices.db")      # INVOICE_DB_PATH
    images_dir: Path = Path("/data/images")           # INVOICE_IMAGES_DIR
    max_upload_bytes: int = 10 * 1024 * 1024          # INVOICE_MAX_UPLOAD_BYTES
    port: int = 8000                                  # INVOICE_PORT
    host: str = "0.0.0.0"                             # bind de Gunicorn en el contenedor (Req. 10.6, 12.1)
    ocr_timeout_seconds: int = 60
    draft_ttl_hours: int = 24

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        """Build settings from ``env``; unset or blank variables keep their defaults.

        Raises ``ConfigError`` if an integer variable is not a positive base-10
        integer written with ASCII digits.
        """
        defaults = cls()
        return cls(
            db_path=_read_path(env, ENV_DB_PATH, defaults.db_path),
            images_dir=_read_path(env, ENV_IMAGES_DIR, defaults.images_dir),
            max_upload_bytes=_read_positive_int(env, ENV_MAX_UPLOAD_BYTES, defaults.max_upload_bytes),
            port=_read_positive_int(env, ENV_PORT, defaults.port),
        )


def _read_raw(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _read_path(env: Mapping[str, str], name: str, default: Path) -> Path:
    raw = _read_raw(env, name)
    return default if raw is None else Path(raw)


def _read_positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _read_raw(env, name)
    if raw is None:
        return default
    # isascii + isdigit rejects signs, decimals, exponents, "_" separators and
    # non-ASCII digits that int() would otherwise accept.
    if not (raw.isascii() and raw.isdigit()):
        raise ConfigError(f"{name} debe ser un entero positivo (valor: {raw!r})")
    value = int(raw)
    if value <= 0:
        raise ConfigError(f"{name} debe ser un entero positivo (valor: {raw!r})")
    return value
