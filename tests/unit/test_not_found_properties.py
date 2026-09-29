"""Property test: missing resources and paths outside the store answer 404 (Property 20).

Each test builds one full application (``create_app``) inside a ``TemporaryDirectory`` and
runs its Hypothesis examples against it; function-scoped pytest fixtures are not reused
across examples. The store holds a real Borrador and a real Apunte, and a "secret" file with a
unique marker lives *outside* the Almacén_Imágenes: no response may ever contain it.

Responses are always closed (``with client.get(...)``) so Windows can remove the directory.

Routing redirects are accepted: the test client decodes ``%2F`` to ``/`` and Werkzeug's
``merge_slashes`` answers 308 (e.g. ``/drafts/0%2F/image`` → ``/drafts/0/image``). GET
requests follow redirects and the *final* status must be 404; POST requests accept only a
308 whose ``Location`` stays under ``/drafts/``, followed by a 404. In every case no response
of the chain may contain the secret marker.

**Validates: Requirements 8.6, 14.3, 14.4**
"""

from __future__ import annotations

import string
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit

from flask import Flask
from flask.testing import FlaskClient
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.config import Settings
from app.storage import DRAFT_ID
from tests.fakes import FakeOcrEngine
from tests.strategies import JPEG_SIGNATURE

NOW = datetime(2025, 6, 15, 10, 30, tzinfo=timezone.utc)
SECRET_MARKER = b"TOP-SECRET-OUTSIDE-THE-STORE-7f3a9c"
SECRET_JPEG = JPEG_SIGNATURE + SECRET_MARKER

VALID_FORM = {
    "invoice_date": "2025-03-01",
    "entry_type": "gasto",
    "base_amount": "100.00",
    "vat_amount": "21.00",
    "total": "121.00",
    "supplier": "Ferretería López",
    "tax_id": "",
    "invoice_number": "F-1",
    "concept": "Tornillos",
    "category": "Hogar",
}

ENTRY_GET_ROUTES = ("/entries/{id}", "/entries/{id}/image", "/entries/{id}/edit", "/entries/{id}/delete")


@dataclass
class Env:
    app: Flask
    client: FlaskClient
    root: Path
    images_dir: Path
    entry_ids: set[int]
    draft_id: str


@contextmanager
def _environment() -> Iterator[Env]:
    """Full app over a fresh temporary directory, with one Apunte, one Borrador and a secret."""
    from app.main import create_app

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = Settings(db_path=root / "db" / "invoices.db", images_dir=root / "images")
        app = create_app(cfg, FakeOcrEngine("Total 121,00"), clock=lambda: NOW)
        services = app.extensions["invoice"]

        # Secrets outside the store: next to images/, and next to tmp/ with draft-like names.
        (root / "secret.jpg").write_bytes(SECRET_JPEG)
        (root / "secret.txt").write_bytes(SECRET_MARKER)
        (cfg.images_dir / "secret.jpg").write_bytes(SECRET_JPEG)  # in root/, not a UUID name

        saved = services["drafts"].create(JPEG_SIGNATURE + b"entry", "jpg")
        result = services["entries"].confirm(saved.draft_id, VALID_FORM, mismatch_confirmed=False)
        assert result.entry_id is not None
        pending = services["drafts"].create(JPEG_SIGNATURE + b"draft", "jpg")

        yield Env(app, app.test_client(), root, cfg.images_dir, {result.entry_id}, pending.draft_id)


MAX_REDIRECTS = 5


def _request(client: FlaskClient, method: str, url: str) -> tuple[int, bytes]:
    status, body, _ = _request_full(client, method, url)
    return status, body


def _request_full(client: FlaskClient, method: str, url: str) -> tuple[int, bytes, str | None]:
    with client.open(url, method=method) as response:
        return response.status_code, response.get_data(), response.headers.get("Location")


def _assert_404_without_secret(client: FlaskClient, method: str, url: str) -> None:
    """Final status must be 404 and no response in the chain may contain the secret.

    GET follows routing redirects (e.g. Werkzeug's ``merge_slashes`` 308). POST only accepts
    a 308 that stays under ``/drafts/`` (method preserved) before the final 404.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        status, body, location = _request_full(client, method, current)
        assert SECRET_MARKER not in body, (method, url, current)
        if status not in (301, 302, 303, 307, 308):
            assert status == 404, (method, url, current, status)
            return
        assert location is not None, (method, url, status)
        parts = urlsplit(location)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        if method != "GET":
            assert status == 308, (method, url, status)
            assert parts.path.startswith("/drafts/"), (method, url, location)
        current = target
    raise AssertionError(f"too many redirects: {method} {url}")


# -- Strategies ---------------------------------------------------------------------------

entry_ids = st.one_of(
    st.integers(min_value=1, max_value=10**6),
    st.integers(min_value=2**31, max_value=2**63 - 1),
    st.integers(min_value=2**63, max_value=10**40),  # beyond SQLite INTEGER
)

_TRAVERSAL = st.sampled_from(
    [
        "..",
        "../",
        "../..",
        "..%2F",
        "..%2F..%2Fsecret.jpg",
        "%2e%2e",
        "%2E%2E%2F",
        "..\\",
        "..\\..\\secret.jpg",
        "\\",
        "/",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "C:/Windows/win.ini",
        "\\\\server\\share\\secret.jpg",
        "secret",
        "secret.jpg",
        "../secret",
        "..\\secret",
        "\x00",
        "..\x00",
        "a" * 32 + "\x00",
        "a" * 32 + "\n",
        "a" * 32 + ".jpg",
        "A" * 32,
        "ａ" * 32,  # full-width letters
        "٠" * 32,  # Arabic-Indic digits
        "‥",  # two-dot leader
        "．．",  # full-width dots
        "%c0%ae%c0%ae",
        "....//",
        ".",
    ]
)


@st.composite
def traversal_ids(draw: st.DrawFn) -> str:
    """Traversal payloads, alone or wrapped in arbitrary text."""
    payload = draw(_TRAVERSAL)
    prefix = draw(st.text(max_size=4))
    suffix = draw(st.sampled_from(["", "", "secret.jpg", "tmp", "/secret.txt", "\\secret.txt"]))
    return prefix + payload + suffix


def draft_segments() -> st.SearchStrategy[str]:
    """Arbitrary draft-id strings: random text, hex-ish near misses and traversal payloads."""
    near_miss = st.text(alphabet=string.hexdigits + "-_.", min_size=0, max_size=40)
    well_formed = st.text(alphabet="0123456789abcdef", min_size=32, max_size=32)  # nonexistent
    return st.one_of(st.text(min_size=1, max_size=64), near_miss, well_formed, traversal_ids())


def _draft_url(env: Env, segment: str, action: str) -> str | None:
    """URL with the segment percent-encoded; ``None`` if it collides with the real Borrador."""
    if segment == env.draft_id:
        return None
    return f"/drafts/{quote(segment, safe='')}/{action}"


# -- Properties ---------------------------------------------------------------------------


def test_nonexistent_entries_answer_404():
    with _environment() as env:
        # Feature: invoice-reader, Property 20: Recursos inexistentes y rutas fuera del almacén responden 404
        @settings(max_examples=100, deadline=None)
        @given(entry_id=entry_ids)
        def check(entry_id: int) -> None:
            assume(entry_id not in env.entry_ids)
            for route in ENTRY_GET_ROUTES:
                _assert_404_without_secret(env.client, "GET", route.format(id=entry_id))

        check()


def test_unknown_or_malformed_draft_ids_answer_404():
    with _environment() as env:
        # Feature: invoice-reader, Property 20: Recursos inexistentes y rutas fuera del almacén responden 404
        @settings(max_examples=100, deadline=None)
        @given(segment=draft_segments())
        def check(segment: str) -> None:
            url = _draft_url(env, segment, "image")
            assume(url is not None)
            _assert_404_without_secret(env.client, "GET", url)

            confirm = _draft_url(env, segment, "confirm")
            _assert_404_without_secret(env.client, "POST", confirm)

            # Cancel is idempotent for a well-formed id (303); malformed ids must be 404.
            if DRAFT_ID.fullmatch(segment) is None:
                _assert_404_without_secret(env.client, "POST", _draft_url(env, segment, "cancel"))

        check()
        # The real Borrador is untouched by all the attempts above.
        assert _request(env.client, "GET", f"/drafts/{env.draft_id}/image")[0] == 200


def test_traversal_names_never_resolve_outside_the_store():
    with _environment() as env:
        store = env.app.extensions["invoice"]["store"]

        # Feature: invoice-reader, Property 20: Recursos inexistentes y rutas fuera del almacén responden 404
        @settings(max_examples=100, deadline=None)
        @given(name=st.one_of(traversal_ids(), st.text(max_size=64)))
        def check(name: str) -> None:
            assert store.resolve(name) is None
            assert store.load_draft(name) is None
            url = _draft_url(env, name, "image")
            if url is not None:
                _assert_404_without_secret(env.client, "GET", url)

        check()
