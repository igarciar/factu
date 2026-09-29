"""Pruebas del blueprint ``entries`` y sus plantillas (Req. 6.6, 8.1-8.6, 9.1-9.3, 14.7).

Se monta una aplicación Flask mínima con las plantillas reales, los servicios reales sobre
``tmp_path`` (SQLite + ``ImageStore``) y un manejador 404 que pinta ``error.html``, como hará
``create_app`` (tarea 12.6).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from flask import Flask, render_template
from flask.testing import FlaskClient

from app.config import Settings
from app.formatting import format_eur
from app.models import EntryFilter, EntryInput
from app.repository import EntryRepository, connect, init_schema
from app.routes.entries import bp, parse_filters
from app.services import MISMATCH_FIELD, EntryService
from app.storage import ImageStore
from app.uploads import JPEG_MAGIC

APP_DIR = Path(__file__).resolve().parents[2] / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
TEMPLATES = ("list.html", "_entries_table.html", "detail.html", "edit.html", "confirm_delete.html")

NOW = datetime(2025, 3, 2, 12, 0, tzinfo=timezone.utc)
JPEG = JPEG_MAGIC + b"\x00" * 32
ROW_LINK = 'aria-label="Ver apunte de'
SCRIPT = "<script>alert(1)</script>"
ESCAPED_SCRIPT = "&lt;script&gt;alert(1)&lt;/script&gt;"

VALID_FORM = {
    "invoice_date": "2025-04-10",
    "entry_type": "ingreso",
    "supplier": "Cliente Nuevo S.L.",
    "tax_id": "",
    "invoice_number": "F-99",
    "concept": "Servicios de marzo",
    "category": "Consultoría",
    "base_amount": "200,00",
    "vat_amount": "42,00",
    "total": "242,00",
}


@dataclass
class Ctx:
    app: Flask
    client: FlaskClient
    settings: Settings
    service: EntryService

    def add(
        self,
        *,
        invoice_date: str = "2025-03-01",
        entry_type: str = "gasto",
        total: str = "121.00",
        base: str | None = "100.00",
        vat: str | None = "21.00",
        supplier: str | None = "Ferretería López",
        concept: str | None = "Tornillos",
        category: str | None = "Hogar",
        tax_id: str | None = "B12345674",
        invoice_number: str | None = "F-1",
        ocr_text: str = "Ferretería López\nTotal 121,00 €",
    ) -> int:
        """Insert an Apunte with its image file and return its id."""
        name = f"{uuid4().hex}.jpg"
        (self.settings.images_dir / name).write_bytes(JPEG)
        entry = EntryInput(
            invoice_date=date.fromisoformat(invoice_date),
            entry_type=entry_type,  # type: ignore[arg-type]
            total=Decimal(total),
            base_amount=None if base is None else Decimal(base),
            vat_amount=None if vat is None else Decimal(vat),
            supplier=supplier,
            tax_id=tax_id,
            invoice_number=invoice_number,
            concept=concept,
            category=category,
        )
        conn = connect(self.settings.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            entry_id = EntryRepository().insert(conn, entry, ocr_text, name, NOW)
            conn.commit()
        finally:
            conn.close()
        return entry_id

    def image_path(self, entry_id: int) -> Path:
        entry = self.service.get(entry_id)
        assert entry is not None
        return self.settings.images_dir / entry.image_filename


@pytest.fixture
def ctx(settings: Settings) -> Ctx:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    init_schema(settings.db_path)
    store = ImageStore(settings.images_dir)
    service = EntryService(store, lambda: connect(settings.db_path))
    app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))
    app.add_template_filter(format_eur, "eur")
    app.extensions["invoice"] = {"settings": settings, "store": store, "entries": service}
    app.register_blueprint(bp)

    @app.errorhandler(404)
    def not_found(_error: Exception) -> tuple[str, int]:
        return render_template("error.html", status_code=404), 404

    return Ctx(app=app, client=app.test_client(), settings=settings, service=service)


def _html(response) -> str:
    return response.get_data(as_text=True)


def _alert_region(html: str) -> str:
    region = re.search(r'<div id="alerts" class="alerts" role="alert">(.*?)</div>', html, re.DOTALL)
    assert region is not None
    return region.group(1)


def _rows(html: str) -> int:
    return html.count(ROW_LINK)


# --------------------------------------------------------------------------------------- #
# Listado (Req. 8.1-8.4)
# --------------------------------------------------------------------------------------- #


def test_list_orders_by_invoice_date_desc_with_columns(ctx: Ctx) -> None:
    ctx.add(invoice_date="2024-01-01", supplier="Viejo", concept="Luz", category="Suministros")
    ctx.add(invoice_date="2025-05-01", supplier="Nuevo", entry_type="ingreso", total="1234.50")
    ctx.add(invoice_date="2025-01-01", supplier="Medio A")
    ctx.add(invoice_date="2025-01-01", supplier="Medio B")  # same date: higher id first

    response = ctx.client.get("/entries")

    html = _html(response)
    assert response.status_code == 200
    assert "<h1>Apuntes</h1>" in html
    positions = [html.index(f"Ver apunte de {name} ") for name in ("Nuevo", "Medio B", "Medio A", "Viejo")]
    assert positions == sorted(positions)
    assert "Página 1 de 1 · 20 por página · 4 apuntes" in html
    assert '<time datetime="2024-01-01">01/01/2024</time>' in html
    for header in ("Fecha", "Proveedor/emisor", "Concepto", "Categoría", "Tipo", "Total"):
        assert f'<th scope="col"' in html and f">{header}</th>" in html
    assert ">Luz</td>" in html and ">Suministros</td>" in html
    assert ">Ingreso</td>" in html and ">Gasto</td>" in html
    assert "1.234,50 €" in html
    assert 'data-label="Total"' in html
    assert "Suma de gastos" not in html  # no filters → no sums (8.4)


def test_list_paginates_20_per_page(ctx: Ctx) -> None:
    for day in range(1, 22):
        ctx.add(invoice_date=f"2025-01-{day:02d}", supplier=f"P{day:02d}")

    first = _html(ctx.client.get("/entries"))
    second = _html(ctx.client.get("/entries?page=2"))

    assert _rows(first) == 20
    assert "Página 1 de 2 · 20 por página · 21 apuntes" in first
    assert 'href="/entries?page=2"' in first
    assert 'hx-get="/entries?page=2"' in first and 'hx-target="#entries"' in first
    assert ">Anterior</a>" not in first
    assert "Ver apunte de P21 " in first and "Ver apunte de P01 " not in first
    assert _rows(second) == 1
    assert "Página 2 de 2 · 20 por página · 21 apuntes" in second
    assert "Ver apunte de P01 " in second
    assert 'href="/entries?page=1"' in second and ">Siguiente</a>" not in second


def test_list_singular_and_empty_state(ctx: Ctx) -> None:
    empty = _html(ctx.client.get("/entries"))
    assert "Página 1 de 1 · 20 por página · 0 apuntes" in empty
    assert "Todavía no hay apuntes." in empty and 'href="/upload"' in empty

    ctx.add()
    one = _html(ctx.client.get("/entries"))
    assert "Página 1 de 1 · 20 por página · 1 apunte</p>" in one


def test_list_page_after_last_offers_first_page(ctx: Ctx) -> None:
    ctx.add()

    html = _html(ctx.client.get("/entries?page=5"))

    assert "Página 5 de 1" in html
    assert _rows(html) == 0
    assert "No hay apuntes en esta página." in html
    assert 'href="/entries">Ir a la página 1</a>' in html


def test_list_filters_and_shows_sums(ctx: Ctx) -> None:
    ctx.add(invoice_date="2025-01-10", entry_type="gasto", total="10.50", supplier="G1", category="Hogar")
    ctx.add(invoice_date="2025-02-10", entry_type="gasto", total="1000.00", supplier="G2", category="Ocio")
    ctx.add(invoice_date="2025-03-10", entry_type="ingreso", total="2000.25", supplier="I1", category="Hogar")
    ctx.add(invoice_date="2024-12-31", entry_type="ingreso", total="5.00", supplier="I0", category="Hogar")

    by_type = _html(ctx.client.get("/entries?type=ingreso"))
    assert {n for n in ("G1", "G2", "I1", "I0") if f"Ver apunte de {n} " in by_type} == {"I1", "I0"}
    assert "Suma de gastos" in by_type and "Suma de ingresos" in by_type
    assert 'data-total="expenses">0,00 €' in by_type
    assert 'data-total="income">2.005,25 €' in by_type
    assert '<option value="ingreso" selected>' in by_type

    combined = _html(
        ctx.client.get("/entries?category=Hogar&date_from=2025-01-01&date_to=2025-03-10")
    )
    assert {n for n in ("G1", "G2", "I1", "I0") if f"Ver apunte de {n} " in combined} == {"G1", "I1"}
    assert "2 apuntes" in combined
    assert 'data-total="expenses">10,50 €' in combined
    assert 'data-total="income">2.000,25 €' in combined
    assert 'value="Hogar"' in combined and 'value="2025-01-01"' in combined and 'value="2025-03-10"' in combined

    none = _html(ctx.client.get("/entries?category=Salud"))
    assert "Ningún apunte cumple los filtros aplicados." in none
    assert 'data-total="expenses">0,00 €' in none


def test_list_keeps_filters_in_pagination_links(ctx: Ctx) -> None:
    for day in range(1, 22):
        ctx.add(invoice_date=f"2025-01-{day:02d}", entry_type="ingreso", supplier=f"I{day:02d}")
    ctx.add(invoice_date="2025-01-15", entry_type="gasto", supplier="Gasto")

    html = _html(ctx.client.get("/entries?type=ingreso"))

    link = re.search(r'href="(/entries\?[^"]*page=2[^"]*)"', html)
    assert link is not None
    assert "type=ingreso" in link.group(1)
    assert "21 apuntes" in html


def test_list_ignores_invalid_parameters(ctx: Ctx) -> None:
    ctx.add(supplier="A")
    ctx.add(supplier="B", entry_type="ingreso")

    response = ctx.client.get(
        "/entries?type=otro&date_from=2025-02-30&date_to=01/03/2025&category=%20%20&page=abc"
    )

    html = _html(response)
    assert response.status_code == 200
    assert "Página 1 de 1 · 20 por página · 2 apuntes" in html
    assert "Suma de gastos" not in html
    for raw_page in ("0", "-1", "1e3", "100001"):
        assert "Página 1 de 1" in _html(ctx.client.get(f"/entries?page={raw_page}"))


def test_parse_filters_normalizes_values() -> None:
    flt, applied = parse_filters(
        {"type": " gasto ", "category": " Hogar ", "date_from": "2025-01-01", "date_to": "٢٠٢٥-01-01"}
    )

    assert flt == EntryFilter(entry_type="gasto", category="Hogar", date_from=date(2025, 1, 1))
    assert applied == {"type": "gasto", "category": "Hogar", "date_from": "2025-01-01"}
    assert parse_filters({}) == (EntryFilter(), {})


def test_list_htmx_request_returns_partial(ctx: Ctx) -> None:
    ctx.add(supplier="Parcial")

    partial = ctx.client.get("/entries?type=gasto", headers={"HX-Request": "true"})
    restore = ctx.client.get(
        "/entries", headers={"HX-Request": "true", "HX-History-Restore-Request": "true"}
    )
    full = ctx.client.get("/entries")

    body = _html(partial)
    assert partial.status_code == 200
    assert "<html" not in body and "<form" not in body
    assert "Ver apunte de Parcial " in body and "Suma de gastos" in body
    assert "HX-Request" in partial.headers.get("Vary", "")
    assert "<html" in _html(restore)
    full_html = _html(full)
    assert "<html" in full_html
    assert 'hx-get="/entries" hx-target="#entries" hx-push-url="true"' in full_html
    assert '<div id="entries"' in full_html
    assert '<datalist id="categories">' in full_html and '<option value="Hogar">' in full_html
    assert "HX-Request" in full.headers.get("Vary", "")


# --------------------------------------------------------------------------------------- #
# Detalle (Req. 6.6, 8.5, 8.6, 14.7)
# --------------------------------------------------------------------------------------- #


def test_detail_shows_all_fields_ocr_text_and_image(ctx: Ctx) -> None:
    entry_id = ctx.add(ocr_text="FERRETERÍA LÓPEZ\nTotal: 121,00 €\n")

    response = ctx.client.get(f"/entries/{entry_id}")

    html = _html(response)
    assert response.status_code == 200
    for text in (
        "01/03/2025", "Gasto", "Ferretería López", "B12345674", "F-1", "Tornillos", "Hogar",
        "100,00 €", "21,00 €", "121,00 €", "02/03/2025 12:00 (UTC)",
    ):
        assert text in html
    assert '<pre class="ocr-text">FERRETERÍA LÓPEZ\nTotal: 121,00 €\n</pre>' in html
    assert f'<img src="/entries/{entry_id}/image"' in html
    assert f'href="/entries/{entry_id}/edit"' in html
    assert f'href="/entries/{entry_id}/delete"' in html
    assert "Apunte guardado correctamente." not in html
    assert "no parece válido" not in html


def test_detail_with_saved_shows_confirmation_message(ctx: Ctx) -> None:
    entry_id = ctx.add()

    html = _html(ctx.client.get(f"/entries/{entry_id}?saved=1"))

    assert '<p class="alert alert--success">Apunte guardado correctamente.</p>' in _alert_region(html)


def test_detail_optional_fields_empty_and_invalid_tax_id_warning(ctx: Ctx) -> None:
    entry_id = ctx.add(base=None, vat=None, supplier=None, concept=None, category=None,
                       invoice_number=None, tax_id="12345678A", ocr_text="  ")

    html = _html(ctx.client.get(f"/entries/{entry_id}"))

    assert "No se detectó texto en la imagen." in html
    assert "El NIF/CIF no parece válido" in html
    assert html.count("<dd>—</dd>") >= 4


def test_supplier_and_ocr_text_are_escaped(ctx: Ctx) -> None:
    entry_id = ctx.add(supplier=SCRIPT, concept=SCRIPT, ocr_text=SCRIPT)

    pages = [
        _html(ctx.client.get("/entries")),
        _html(ctx.client.get(f"/entries/{entry_id}")),
        _html(ctx.client.get(f"/entries/{entry_id}/edit")),
        _html(ctx.client.get(f"/entries/{entry_id}/delete")),
    ]

    for html in pages:
        assert SCRIPT not in html
        assert ESCAPED_SCRIPT in html


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/entries/999"),
        ("get", "/entries/999/edit"),
        ("post", "/entries/999/edit"),
        ("get", "/entries/999/delete"),
        ("post", "/entries/999/delete"),
        ("get", "/entries/abc"),
        ("get", "/entries/-1"),
        # Beyond SQLite INTEGER (2**63 - 1): must not overflow into a 500.
        ("get", f"/entries/{2**63}"),
        ("get", f"/entries/{2**63}/edit"),
        ("post", f"/entries/{2**63}/edit"),
        ("get", f"/entries/{2**63}/delete"),
        ("post", f"/entries/{10**40}/delete"),
    ],
)
def test_missing_entry_responds_404_with_error_page(ctx: Ctx, method: str, path: str) -> None:
    response = getattr(ctx.client, method)(path, data=VALID_FORM if method == "post" else None)

    assert response.status_code == 404
    assert "Página no encontrada" in _html(response)


# --------------------------------------------------------------------------------------- #
# Edición (Req. 9.1, 5.x)
# --------------------------------------------------------------------------------------- #


def test_edit_get_prefills_form(ctx: Ctx) -> None:
    entry_id = ctx.add()

    response = ctx.client.get(f"/entries/{entry_id}/edit")

    html = _html(response)
    assert response.status_code == 200
    assert f'<form method="post" action="/entries/{entry_id}/edit"' in html
    for name, value in (
        ("invoice_date", "2025-03-01"), ("supplier", "Ferretería López"), ("tax_id", "B12345674"),
        ("invoice_number", "F-1"), ("concept", "Tornillos"), ("category", "Hogar"),
        ("base_amount", "100.00"), ("vat_amount", "21.00"), ("total", "121.00"),
    ):
        assert re.search(rf'name="{name}" value="{re.escape(value)}"', html), name
    assert '<option value="gasto" selected>' in html
    assert 'list="categories"' in html and '<option value="Suministros">' in html
    assert f'name="{MISMATCH_FIELD}"' not in html
    assert f'<img src="/entries/{entry_id}/image"' in html
    assert 'aria-invalid="true"' not in html


def test_edit_post_valid_updates_and_redirects_to_detail(ctx: Ctx) -> None:
    entry_id = ctx.add()

    response = ctx.client.post(f"/entries/{entry_id}/edit", data=VALID_FORM)

    assert response.status_code == 303
    assert response.headers["Location"] == f"/entries/{entry_id}?saved=1"
    entry = ctx.service.get(entry_id)
    assert entry is not None
    assert (entry.invoice_date, entry.entry_type, entry.total) == (date(2025, 4, 10), "ingreso", Decimal("242.00"))
    assert (entry.supplier, entry.category, entry.tax_id) == ("Cliente Nuevo S.L.", "Consultoría", None)
    assert "Consultoría" in ctx.service.category_names()
    detail = _html(ctx.client.get(response.headers["Location"]))
    assert "Apunte guardado correctamente." in _alert_region(detail)


def test_edit_post_invalid_returns_422_and_keeps_values(ctx: Ctx) -> None:
    entry_id = ctx.add()
    form = {**VALID_FORM, "invoice_date": "2025-02-30", "total": "-5", "supplier": "Nuevo <b>nombre</b>"}

    response = ctx.client.post(f"/entries/{entry_id}/edit", data=form)

    html = _html(response)
    assert response.status_code == 422
    assert 'name="invoice_date" value="2025-02-30"' in html
    assert 'name="total" value="-5"' in html
    assert 'name="supplier" value="Nuevo &lt;b&gt;nombre&lt;/b&gt;"' in html
    assert '<option value="ingreso" selected>' in html
    assert 'aria-invalid="true" aria-describedby="invoice_date-error"' in html
    assert 'id="total-error"' in html
    assert "Revisa los campos marcados: 2 errores." in _alert_region(html)
    entry = ctx.service.get(entry_id)
    assert entry is not None and entry.total == Decimal("121.00")  # unchanged


def test_edit_mismatch_needs_confirmation(ctx: Ctx) -> None:
    entry_id = ctx.add()
    form = {**VALID_FORM, "total": "300,00"}

    blocked = ctx.client.post(f"/entries/{entry_id}/edit", data=form)

    html = _html(blocked)
    assert blocked.status_code == 422
    assert "no coincide con el total" in _alert_region(html)
    assert f'type="checkbox" id="{MISMATCH_FIELD}" name="{MISMATCH_FIELD}" value="1">' in html
    assert ctx.service.get(entry_id).total == Decimal("121.00")  # type: ignore[union-attr]

    confirmed = ctx.client.post(f"/entries/{entry_id}/edit", data={**form, MISMATCH_FIELD: "1"})

    assert confirmed.status_code == 303
    assert ctx.service.get(entry_id).total == Decimal("300.00")  # type: ignore[union-attr]


def test_edit_keeps_mismatch_checkbox_checked_on_other_errors(ctx: Ctx) -> None:
    entry_id = ctx.add()
    form = {**VALID_FORM, "total": "300,00", "invoice_date": "", MISMATCH_FIELD: "1"}

    html = _html(ctx.client.post(f"/entries/{entry_id}/edit", data=form))

    assert f'name="{MISMATCH_FIELD}" value="1" checked>' in html
    assert "Este campo es obligatorio." in html


def test_edit_save_failure_returns_500_with_values(ctx: Ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    entry_id = ctx.add()

    def failing_update(*_args: object, **_kwargs: object) -> bool:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ctx.service.entries, "update", failing_update)

    response = ctx.client.post(f"/entries/{entry_id}/edit", data=VALID_FORM)

    html = _html(response)
    assert response.status_code == 500
    assert "No se ha podido guardar, inténtalo de nuevo." in _alert_region(html)
    assert 'name="supplier" value="Cliente Nuevo S.L."' in html


# --------------------------------------------------------------------------------------- #
# Borrado (Req. 9.2, 9.3)
# --------------------------------------------------------------------------------------- #


def test_delete_get_asks_for_confirmation_without_deleting(ctx: Ctx) -> None:
    entry_id = ctx.add()

    response = ctx.client.get(f"/entries/{entry_id}/delete")

    html = _html(response)
    assert response.status_code == 200
    assert "¿Eliminar este apunte?" in html
    assert f'<form method="post" action="/entries/{entry_id}/delete">' in html
    assert "Sí, eliminar el apunte" in html
    assert f'href="/entries/{entry_id}"' in html
    assert ctx.service.get(entry_id) is not None


def test_delete_post_removes_entry_and_image(ctx: Ctx) -> None:
    entry_id = ctx.add()
    image = ctx.image_path(entry_id)
    assert image.is_file()

    response = ctx.client.post(f"/entries/{entry_id}/delete")

    assert response.status_code == 303
    assert response.headers["Location"] == "/entries"
    assert ctx.service.get(entry_id) is None
    assert not image.exists()
    assert ctx.client.get(f"/entries/{entry_id}").status_code == 404


# --------------------------------------------------------------------------------------- #
# Plantillas: sin JS/CSS en línea (CSP) ni URLs externas
# --------------------------------------------------------------------------------------- #


@pytest.mark.parametrize("template", TEMPLATES)
def test_templates_have_no_inline_scripts_styles_or_external_urls(template: str) -> None:
    source = (TEMPLATES_DIR / template).read_text(encoding="utf-8")

    assert not re.search(r"https?://", source)
    assert not re.search(r"\sstyle\s*=", source, re.IGNORECASE)
    assert "<style" not in source.lower() and "<script" not in source.lower()
    assert not re.search(r"\son[a-z]+\s*=", source, re.IGNORECASE)
    assert "|safe" not in source.replace(" ", "")
