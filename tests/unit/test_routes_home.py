"""Unit tests of the Portada: ``GET /`` and ``GET /recent`` (tasks 12.11, 12.12).

The Flask app is built here with the ``pages`` blueprint, the security headers, the ``eur``
filter and the real services over ``tmp_path`` (``FakeOcrEngine``, fixed clock in 2025), plus
a 404 handler equivalent to the one of ``app.main``.

Requirements: 1.1, 14.7, 16.1, 16.5, 16.7-16.18.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import Flask, render_template

import app as app_package
from app import security
from app.config import Settings
from app.formatting import MONTH_NAMES_ES, format_eur
from app.models import EntryInput
from app.repository import EntryRepository, connect, init_schema
from app.routes import pages
from app.services import DashboardService, DraftPurger, DraftService, EntryService
from app.storage import ImageStore
from tests.fakes import FakeOcrEngine

APP_DIR = Path(app_package.__file__).parent
FIXED_NOW = datetime(2025, 6, 15, 10, 0, tzinfo=timezone.utc)  # Año_Actual 2025, anterior 2024
ZERO = "0,00 €"


# --------------------------------------------------------------------------- helpers


class _TopLevel(HTMLParser):
    """Top-level start tags of a fragment, plus every element with its attributes."""

    VOID = {"input", "img", "meta", "link", "br", "hr", "source"}

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.top: list[str] = []
        self.elements: list[tuple[str, dict[str, str | None], int]] = []

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            self.top.append(tag)
        self.elements.append((tag, dict(attrs), self.depth))
        if tag not in self.VOID:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag not in self.VOID:
            self.depth -= 1


def parse_fragment(html: str) -> _TopLevel:
    parser = _TopLevel()
    parser.feed(html)
    return parser


def summary_section(html: str) -> str:
    """HTML of the Resumen_Gastos (before the Últimas_Facturas heading)."""
    return html.split('id="recent-title"', 1)[0]


def recent_section(html: str) -> str:
    return html.split('id="recent-title"', 1)[1]


def recent_row_count(html: str) -> int:
    match = re.search(r'<tbody id="recent-rows">(.*?)</tbody>', html, re.S)
    return 0 if match is None else match.group(1).count("<tr>")


def more_block(html: str) -> str:
    match = re.search(r'<div id="recent-more"[^>]*>(.*?)</div>', html, re.S)
    assert match is not None, "#recent-more missing"
    return match.group(1)


class Env:
    def __init__(self, flask_app: Flask, settings: Settings):
        self.app = flask_app
        self.settings = settings
        self.client = flask_app.test_client()
        self._seq = 0

    def add(
        self,
        *,
        invoice_date: date = date(2025, 3, 10),
        total: str = "10.00",
        entry_type: str = "gasto",
        supplier: str | None = "Proveedor",
        concept: str | None = "Concepto",
        category: str | None = "Hogar",
    ) -> int:
        """Insert an Apunte; each one gets a later Fecha_Alta than the previous one."""
        self._seq += 1
        conn = connect(self.settings.db_path)
        try:
            entry_id = EntryRepository().insert(
                conn,
                EntryInput(
                    invoice_date=invoice_date,
                    entry_type=entry_type,  # type: ignore[arg-type]
                    total=Decimal(total),
                    supplier=supplier,
                    concept=concept,
                    category=category,
                ),
                "texto",
                f"img-{self._seq:04d}.jpg",
                FIXED_NOW + timedelta(seconds=self._seq),
            )
            conn.commit()
        finally:
            conn.close()
        return entry_id

    def add_many(self, count: int) -> list[int]:
        return [self.add(supplier=f"Proveedor {i:02d}") for i in range(count)]


@pytest.fixture
def env(settings: Settings) -> Env:
    flask_app = Flask(
        __name__,
        template_folder=str(APP_DIR / "templates"),
        static_folder=str(APP_DIR / "static"),
    )
    security.register(flask_app)
    flask_app.add_template_filter(format_eur, "eur")
    flask_app.register_blueprint(pages.bp)

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    init_schema(settings.db_path)
    store = ImageStore(settings.images_dir)
    ocr = FakeOcrEngine(text="")

    def connect_db():
        return connect(settings.db_path)

    def fixed_clock() -> datetime:
        return FIXED_NOW

    flask_app.extensions["invoice"] = {
        "settings": settings,
        "store": store,
        "drafts": DraftService(store, ocr),
        "entries": EntryService(store, connect_db),
        "dashboard": DashboardService(EntryRepository(), connect_db, clock=fixed_clock),
        "purger": DraftPurger(store),
        "ocr_status": ocr.status(),
    }

    @flask_app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", status_code=404), 404

    return Env(flask_app, settings)


# --------------------------------------------------------------------------- GET /


def test_empty_database_shows_invitation_and_zero_amounts(env: Env):
    response = env.client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Todavía no hay facturas" in html
    assert '<a href="/upload">Sube la primera.</a>' in html
    summary = summary_section(html)
    assert f'data-total="previous">{ZERO}<' in summary
    assert f'data-total="current">{ZERO}<' in summary
    assert summary.count(f'<td class="num">{ZERO}</td>') == 24
    assert recent_row_count(html) == 0
    assert "Ver siguientes" not in html
    assert "Anteriores" not in html


def test_summary_has_twelve_months_and_spanish_format(env: Env):
    env.add(invoice_date=date(2025, 3, 10), total="1234.56")
    env.add(invoice_date=date(2024, 3, 5), total="100.00")

    html = env.client.get("/").get_data(as_text=True)
    summary = summary_section(html)

    assert re.findall(r'<th scope="row">(\w+)</th>', summary) == list(MONTH_NAMES_ES)
    assert '<th scope="col" class="num">2024</th>' in summary
    assert '<th scope="col" class="num">2025</th>' in summary
    assert "<caption>" in summary
    assert 'data-total="current">1.234,56 €<' in summary
    assert 'data-total="previous">100,00 €<' in summary
    march = re.search(r'<th scope="row">Marzo</th>\s*<td class="num">(.*?)</td>\s*<td class="num">(.*?)</td>', summary)
    assert march is not None and march.groups() == ("100,00 €", "1.234,56 €")


def test_upload_button_links_to_upload(env: Env):
    html = env.client.get("/").get_data(as_text=True)

    assert re.search(r'<a class="button button--primary-large" href="/upload">Subir factura</a>', html)


def test_first_page_shows_ten_rows_and_see_more(env: Env):
    ids = env.add_many(11)

    html = env.client.get("/").get_data(as_text=True)

    assert recent_row_count(html) == 10
    block = more_block(html)
    assert 'href="/?recent_page=2"' in block
    assert 'hx-get="/recent?page=2"' in block
    assert 'hx-target="#recent-rows"' in block
    assert 'hx-swap="beforeend"' in block
    assert "Ver siguientes" in block
    assert "Anteriores" not in html
    # Newest first; every row links to its detail page (16.9).
    assert f'href="/entries/{ids[-1]}"' in html
    assert f'href="/entries/{ids[0]}"' not in html


def test_second_page_shows_remaining_row_and_previous_link(env: Env):
    ids = env.add_many(11)

    html = env.client.get("/?recent_page=2").get_data(as_text=True)

    assert recent_row_count(html) == 1
    assert "Proveedor 00" in html
    assert f'href="/entries/{ids[0]}"' in html
    assert '<a href="/?recent_page=1" rel="prev">Anteriores</a>' in html
    assert "Ver siguientes" not in html


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "1e3", "100001", ""])
def test_invalid_recent_page_falls_back_to_first_page(env: Env, raw: str):
    env.add_many(11)

    html = env.client.get(f"/?recent_page={raw}").get_data(as_text=True)

    assert recent_row_count(html) == 10
    assert 'hx-get="/recent?page=2"' in html
    assert "Anteriores" not in html


def test_page_past_the_end_shows_no_more_invoices(env: Env):
    env.add_many(3)

    html = env.client.get("/?recent_page=99").get_data(as_text=True)

    assert "No hay más facturas" in html
    assert '<a href="/">Ir a la página 1</a>' in html
    assert recent_row_count(html) == 0
    assert "Ver siguientes" not in html
    assert "Todavía no hay facturas" not in html


def test_income_listed_in_recent_but_excluded_from_summary(env: Env):
    env.add(invoice_date=date(2025, 3, 20), total="5000.00", entry_type="ingreso")
    env.add(invoice_date=date(2025, 3, 21), total="12.00", entry_type="gasto")

    html = env.client.get("/").get_data(as_text=True)

    assert "5.000,00 €" not in summary_section(html)
    assert 'data-total="current">12,00 €<' in html
    recent = recent_section(html)
    assert "5.000,00 €" in recent
    assert "Ingreso" in recent and "Gasto" in recent


def test_recent_row_shows_required_columns(env: Env):
    env.add(invoice_date=date(2025, 2, 1), total="42.50", supplier="ACME", concept="Tornillos", category="Hogar")

    recent = recent_section(env.client.get("/").get_data(as_text=True))

    for label, value in (
        ("Proveedor/emisor", "ACME"),
        ("Concepto", "Tornillos"),
        ("Categoría", "Hogar"),
        ("Tipo", "Gasto"),
    ):
        assert f'<td data-label="{label}">{value}</td>' in recent
    assert '<td data-label="Total" class="num">42,50 €</td>' in recent
    assert '<time datetime="2025-02-01">01/02/2025</time>' in recent


def test_supplier_is_escaped(env: Env):
    env.add(supplier="<script>alert(1)</script>")

    html = env.client.get("/").get_data(as_text=True)

    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_home_calls_maybe_purge(env: Env, monkeypatch: pytest.MonkeyPatch):
    purger = env.app.extensions["invoice"]["purger"]
    calls: list[int] = []
    original = purger.maybe_purge

    def spy():
        calls.append(1)
        return original()

    monkeypatch.setattr(purger, "maybe_purge", spy)

    assert env.client.get("/").status_code == 200
    assert calls == [1]


# --------------------------------------------------------------------------- GET /recent


def test_recent_with_htmx_returns_rows_and_oob_more_block(env: Env):
    ids = env.add_many(11)

    response = env.client.get("/recent?page=2", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "HX-Request" in response.headers.get("Vary", "")
    html = response.get_data(as_text=True)
    fragment = parse_fragment(html)
    assert fragment.top == ["tr", "template"]
    oob = [attrs for tag, attrs, _ in fragment.elements if attrs.get("id") == "recent-more"]
    assert oob == [{"id": "recent-more", "hx-swap-oob": "outerHTML"}]
    assert f'href="/entries/{ids[0]}"' in html
    assert "<table" not in html and "<html" not in html and "<tbody" not in html
    # Last page: the out-of-band block empties "Ver siguientes" (16.14).
    assert "Ver siguientes" not in html


def test_recent_with_htmx_keeps_see_more_while_there_are_more(env: Env):
    env.add_many(21)

    html = env.client.get("/recent?page=2", headers={"HX-Request": "true"}).get_data(as_text=True)

    assert parse_fragment(html).top == ["tr"] * 10 + ["template"]
    block = more_block(html)
    assert 'href="/?recent_page=3"' in block
    assert 'hx-get="/recent?page=3"' in block


@pytest.mark.parametrize(("query", "location"), [("page=2", "/?recent_page=2"), ("page=abc", "/?recent_page=1")])
def test_recent_without_htmx_redirects_to_home(env: Env, query: str, location: str):
    response = env.client.get(f"/recent?{query}")

    assert response.status_code == 303
    assert response.headers["Location"] == location
