"""Pruebas de ``base.html``, ``error.html`` y los estáticos (Req. 1.1, 2.2, 12.4, 14.7, 16.1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import Flask, render_template

APP_DIR = Path(__file__).resolve().parents[2] / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
HTMX_VERSION = "2.0.4"

SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)


@pytest.fixture
def app() -> Flask:
    """Aplicación mínima con las carpetas reales de plantillas y estáticos."""
    return Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
    )


def _render(app: Flask, template: str, path: str = "/", **context: object) -> str:
    with app.test_request_context(path):
        return render_template(template, **context)


def test_base_has_lang_viewport_and_skip_link(app: Flask) -> None:
    html = _render(app, "base.html")

    assert '<html lang="es">' in html
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert '<a class="skip-link" href="#main">' in html
    assert '<main id="main"' in html


def test_base_has_main_navigation_links(app: Flask) -> None:
    html = _render(app, "base.html")

    nav = re.search(r'<nav[^>]*aria-label="Principal"[^>]*>(.*?)</nav>', html, re.DOTALL)
    assert nav is not None
    links = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', nav.group(1))
    assert links == [("/", "Inicio"), ("/upload", "Subir factura"), ("/entries", "Apuntes")]


def test_base_marks_current_page_in_navigation(app: Flask) -> None:
    html = _render(app, "base.html", path="/upload")

    assert '<a href="/upload" aria-current="page">Subir factura</a>' in html
    assert '<a href="/" aria-current' not in html


def test_base_serves_css_and_htmx_locally(app: Flask) -> None:
    html = _render(app, "base.html")

    assert '<link rel="stylesheet" href="/static/app.css">' in html
    assert '<script src="/static/htmx.min.js" defer></script>' in html


def test_base_renders_messages_in_alert_region_escaped(app: Flask) -> None:
    html = _render(app, "base.html", messages=["Apunte guardado", "<script>alert(1)</script>"])

    region = re.search(r'<div id="alerts" class="alerts" role="alert">(.*?)</div>', html, re.DOTALL)
    assert region is not None
    assert "Apunte guardado" in region.group(1)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in region.group(1)
    assert "<script>alert(1)</script>" not in html


def test_base_alert_region_present_without_messages(app: Flask) -> None:
    html = _render(app, "base.html")

    assert 'role="alert"' in html
    assert '<p class="alert">' not in html


def test_child_template_blocks_are_rendered(app: Flask) -> None:
    source = (
        '{% extends "base.html" %}'
        "{% block title %}Prueba{% endblock %}"
        "{% block content %}<h1>{{ heading }}</h1>{% endblock %}"
        "{% block scripts %}<script src=\"/static/extra.js\"></script>{% endblock %}"
    )
    with app.test_request_context("/"):
        html = app.jinja_env.from_string(source).render(heading="<b>Hola</b>")

    assert "<title>Prueba · Invoice Reader</title>" in html
    assert "<h1>&lt;b&gt;Hola&lt;/b&gt;</h1>" in html
    assert html.index('<script src="/static/extra.js">') > html.index("</main>")


@pytest.mark.parametrize(
    ("status_code", "expected_title", "expected_text"),
    [
        (404, "Página no encontrada", "No se ha encontrado la página"),
        (500, "Algo ha fallado", "Se ha producido un error inesperado"),
    ],
)
def test_error_page_by_status(app: Flask, status_code: int, expected_title: str, expected_text: str) -> None:
    html = _render(app, "error.html", status_code=status_code)

    assert expected_title in html
    assert f"Error {status_code}" in html
    assert expected_text in html
    assert '<a class="button" href="/">Volver al inicio</a>' in html
    assert 'aria-label="Principal"' in html


def test_error_page_defaults_to_500_and_escapes_message(app: Flask) -> None:
    html = _render(app, "error.html", message="<img src=x onerror=alert(1)>")

    assert "Error 500" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x" not in html


@pytest.mark.parametrize("template", ["base.html", "error.html"])
def test_templates_have_no_inline_scripts_styles_or_external_urls(template: str) -> None:
    source = (TEMPLATES_DIR / template).read_text(encoding="utf-8")

    assert not re.search(r"https?://", source)
    assert not re.search(r"\sstyle\s*=", source, re.IGNORECASE)
    assert "<style" not in source.lower()
    assert not re.search(r"\son[a-z]+\s*=", source, re.IGNORECASE)
    for attrs, body in SCRIPT_TAG.findall(source):
        assert "src=" in attrs
        assert body.strip() == ""


def test_rendered_base_scripts_are_external_only(app: Flask) -> None:
    html = _render(app, "base.html")

    scripts = SCRIPT_TAG.findall(html)
    assert scripts
    for attrs, body in scripts:
        assert 'src="/static/' in attrs
        assert body.strip() == ""


def test_static_files_are_served(app: Flask) -> None:
    client = app.test_client()

    css = client.get("/static/app.css")
    js = client.get("/static/htmx.min.js")

    assert css.status_code == 200
    assert js.status_code == 200
    css.close()
    js.close()


def test_htmx_is_vendored_with_pinned_version() -> None:
    htmx = STATIC_DIR / "htmx.min.js"

    assert htmx.is_file()
    content = htmx.read_text(encoding="utf-8")
    assert len(content) > 10_000
    assert f'version:"{HTMX_VERSION}"' in content


def test_css_has_responsive_layout_and_accessibility_rules() -> None:
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")

    assert "@media (max-width: 639.98px)" in css
    assert "@media (min-width: 640px)" in css
    assert ":focus-visible" in css
    assert re.search(r"\.table-scroll\s*\{[^}]*overflow-x:\s*auto", css)
    assert ".field--missing" in css
    assert '[aria-invalid="true"]' in css
    assert "attr(data-label)" in css
    assert "#recent-more .button" in css
