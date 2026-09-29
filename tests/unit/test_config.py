"""Unit tests for app.config (Requirements 1.3, 11.1, 11.2, 11.3, 12.1, 12.2)."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.config import ConfigError, Settings


def test_defaults():
    s = Settings()
    assert s.db_path == Path("/data/db/invoices.db")
    assert s.images_dir == Path("/data/images")
    assert s.max_upload_bytes == 10 * 1024 * 1024
    assert s.port == 8000
    assert s.host == "0.0.0.0"
    assert s.ocr_timeout_seconds == 60
    assert s.draft_ttl_hours == 24


def test_from_env_empty_mapping_uses_defaults():
    assert Settings.from_env({}) == Settings()


def test_from_env_blank_values_use_defaults():
    env = {
        "INVOICE_DB_PATH": "",
        "INVOICE_IMAGES_DIR": "  ",
        "INVOICE_MAX_UPLOAD_BYTES": "",
        "INVOICE_PORT": " ",
    }
    assert Settings.from_env(env) == Settings()


def test_from_env_reads_all_variables(tmp_path):
    env = {
        "INVOICE_DB_PATH": str(tmp_path / "db" / "x.db"),
        "INVOICE_IMAGES_DIR": str(tmp_path / "img"),
        "INVOICE_MAX_UPLOAD_BYTES": "2048",
        "INVOICE_PORT": " 9000 ",
    }
    s = Settings.from_env(env)
    assert s.db_path == tmp_path / "db" / "x.db"
    assert s.images_dir == tmp_path / "img"
    assert s.max_upload_bytes == 2048
    assert s.port == 9000
    # Not configurable from the environment.
    assert s.host == "0.0.0.0"
    assert s.ocr_timeout_seconds == 60
    assert s.draft_ttl_hours == 24


def test_from_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("INVOICE_PORT", "8123")
    monkeypatch.delenv("INVOICE_MAX_UPLOAD_BYTES", raising=False)
    s = Settings.from_env()
    assert s.port == 8123
    assert s.max_upload_bytes == 10 * 1024 * 1024


@pytest.mark.parametrize("name", ["INVOICE_MAX_UPLOAD_BYTES", "INVOICE_PORT"])
@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5", "1e3", "+5", "1_000", "\u0663"])
def test_from_env_invalid_integer_raises(name, value):
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env({name: value})
    assert name in str(exc_info.value)
    assert exc_info.value.path_or_message == str(exc_info.value)


def test_settings_is_frozen():
    with pytest.raises(FrozenInstanceError):
        Settings().port = 1  # type: ignore[misc]
