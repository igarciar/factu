"""Unit tests for the SQLite connection, schema and CategoryRepository (task 8.1).

Requirements: 7.1, 7.3, 10.5, 14.6.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing

import pytest

from app import repository
from app.repository import SEED_CATEGORIES, CategoryRepository, connect, init_schema

EXPECTED_SEED = {
    "Suministros",
    "Alimentación",
    "Transporte",
    "Hogar",
    "Salud",
    "Ocio",
    "Servicios profesionales",
    "Impuestos",
    "Otros",
}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "invoices.db"
    init_schema(path)
    return path


@pytest.fixture
def conn(db_path):
    # closing() so Windows can remove tmp_path (WAL/SHM files) afterwards.
    with closing(connect(db_path)) as c:
        yield c


def _valid_entry(**overrides):
    row = {
        "invoice_date": "2025-03-01",
        "entry_type": "gasto",
        "total_cents": 1210,
        "image_filename": "a" * 32 + ".jpg",
        "created_at": "2025-03-01T10:15:00.123456+00:00",
    }
    row.update(overrides)
    return row


def _insert_entry(conn, **overrides):
    row = _valid_entry(**overrides)
    columns = ", ".join(row)  # fixed test-controlled column names
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO entries ({columns}) VALUES ({marks})", tuple(row.values()))


# --- connection -------------------------------------------------------------

def test_connect_sets_pragmas_and_explicit_transactions(db_path):
    with closing(connect(db_path)) as c:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert c.isolation_level is None
        # No implicit transaction is opened by DML.
        _insert_entry(c)
        assert not c.in_transaction


def test_connect_closes_connection_if_pragma_fails(tmp_path, monkeypatch):
    closed = []

    class FailingConnection:
        def execute(self, sql):
            raise sqlite3.OperationalError("boom")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(repository.sqlite3, "connect", lambda *a, **k: FailingConnection())
    with pytest.raises(sqlite3.OperationalError):
        connect(tmp_path / "x.db")
    assert closed == [True]


class _WalConn:
    """Minimal connection double: reports a non-WAL mode and fails the switch N times."""

    def __init__(self, failures, message="database is locked"):
        self.failures = failures
        self.message = message
        self.switch_attempts = 0

    def execute(self, sql):
        if sql == "PRAGMA journal_mode":
            return self
        self.switch_attempts += 1
        if self.failures:
            self.failures -= 1
            raise sqlite3.OperationalError(self.message)
        return self

    def fetchone(self):
        return ("delete",)


def test_enable_wal_retries_while_locked(monkeypatch):
    monkeypatch.setattr(repository, "_WAL_RETRY_SECONDS", 0)
    fake = _WalConn(failures=2)
    repository._enable_wal(fake)
    assert fake.switch_attempts == 3


def test_enable_wal_reraises_other_errors():
    fake = _WalConn(failures=1, message="disk I/O error")
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        repository._enable_wal(fake)
    assert fake.switch_attempts == 1


def test_enable_wal_gives_up_after_timeout(monkeypatch):
    monkeypatch.setattr(repository, "BUSY_TIMEOUT_SECONDS", 0)
    fake = _WalConn(failures=10)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        repository._enable_wal(fake)


# --- schema -----------------------------------------------------------------

def test_init_schema_creates_tables_and_indexes(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entries", "categories"} <= tables
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(entries)")}
    assert {"ix_entries_date", "ix_entries_created", "ix_entries_type_date"} <= indexes


def test_seed_categories_present(conn):
    assert set(SEED_CATEGORIES) == EXPECTED_SEED
    assert set(CategoryRepository().list(conn)) == EXPECTED_SEED


def test_init_schema_is_idempotent_and_keeps_data(db_path):
    with closing(connect(db_path)) as c:
        _insert_entry(c)
        CategoryRepository().ensure(c, "Mascotas")
    init_schema(db_path)
    init_schema(db_path)
    with closing(connect(db_path)) as c:
        assert c.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
        names = CategoryRepository().list(c)
    assert len(names) == len(set(names)) == len(EXPECTED_SEED) + 1
    assert "Mascotas" in names


def test_init_schema_concurrent_calls(tmp_path):
    path = tmp_path / "concurrent.db"
    errors: list[BaseException] = []

    def run():
        try:
            init_schema(path)
        except BaseException as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with closing(connect(path)) as c:
        assert len(CategoryRepository().list(c)) == len(EXPECTED_SEED)


def test_init_schema_rolls_back_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    monkeypatch.setattr(
        repository,
        "_SCHEMA_STATEMENTS",
        ("CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY)", "NOT VALID SQL"),
    )
    with pytest.raises(sqlite3.OperationalError):
        init_schema(path)
    with closing(sqlite3.connect(path)) as c:
        tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert tables == []


# --- constraints ------------------------------------------------------------

def test_valid_entry_is_accepted(conn):
    _insert_entry(conn, base_cents=1000, vat_cents=210)
    assert conn.execute("SELECT ocr_text, updated_at FROM entries").fetchone() == ("", None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"entry_type": "otro"},
        {"total_cents": -1},
        {"base_cents": -1},
        {"vat_cents": -1},
        {"total_cents": None},
        {"invoice_date": None},
        {"image_filename": None},
        {"created_at": None},
    ],
)
def test_entry_constraints_reject_invalid_rows(conn, overrides):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_entry(conn, **overrides)


def test_image_filename_is_unique(conn):
    _insert_entry(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_entry(conn)


# --- CategoryRepository -----------------------------------------------------

def test_list_is_sorted_case_insensitively(conn):
    repo = CategoryRepository()
    repo.ensure(conn, "zapatería")
    repo.ensure(conn, "banco")
    names = repo.list(conn)
    assert names == sorted(names, key=str.lower)


def test_ensure_adds_new_category_once(conn):
    repo = CategoryRepository()
    repo.ensure(conn, "  Mascotas  ")
    repo.ensure(conn, "Mascotas")
    repo.ensure(conn, "MASCOTAS")
    names = repo.list(conn)
    assert names.count("Mascotas") == 1
    assert not any(n.lower() == "mascotas" and n != "Mascotas" for n in names)


def test_ensure_existing_seed_ignoring_case_is_noop(conn):
    repo = CategoryRepository()
    repo.ensure(conn, "hogar")
    assert set(repo.list(conn)) == EXPECTED_SEED


@pytest.mark.parametrize(
    ("first", "second"),
    [("Ñ", "ñ"), ("Ñandú", "ñandú"), ("ALIMENTACIÓN", "alimentación"), ("Straße", "STRASSE")],
)
def test_ensure_ignores_non_ascii_case_variants(conn, first, second):
    repo = CategoryRepository()
    repo.ensure(conn, first)
    repo.ensure(conn, second)
    names = repo.list(conn)
    assert sum(n.casefold() == first.casefold() for n in names) == 1


def test_ensure_accented_seed_upper_case_is_noop(conn):
    repo = CategoryRepository()
    repo.ensure(conn, "ALIMENTACIÓN")
    assert set(repo.list(conn)) == EXPECTED_SEED


def test_ensure_blank_name_is_ignored(conn):
    repo = CategoryRepository()
    repo.ensure(conn, "   ")
    assert set(repo.list(conn)) == EXPECTED_SEED


def test_ensure_treats_sql_as_data(conn):
    repo = CategoryRepository()
    nasty = "x'); DROP TABLE categories; --"
    repo.ensure(conn, nasty)
    assert nasty in repo.list(conn)


def test_ensure_participates_in_caller_transaction(conn):
    repo = CategoryRepository()
    conn.execute("BEGIN IMMEDIATE")
    repo.ensure(conn, "Temporal")
    conn.rollback()
    assert "Temporal" not in repo.list(conn)
