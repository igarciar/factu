"""Property test of the review page: it reflects the Borrador and escapes HTML (task 12.7).

Requirements: 4.1, 4.3, 14.7.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from flask import Flask
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from markupsafe import escape

from app.config import Settings
from app.extractor import FIELDS, extract
from app.main import create_app
from app.services import empty_draft
from app.uploads import JPEG_MAGIC
from tests.conftest import FIXED_NOW
from tests.fakes import FakeOcrEngine
from tests.strategies import amounts, dates, noise_words, valid_tax_ids

JPEG = JPEG_MAGIC + b"\x00\x10JFIF" + bytes(range(64))

# Payloads with HTML special characters (<, >, &, ", ') that must never reach the page raw.
PAYLOADS = (
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '"><b>x</b>',
    "' onmouseover='alert(1)",
    "</pre><script>x</script>",
    "Tom & \"Jerry\" <S.L.>",
    "&lt;script&gt;",
)
# "&lt;script&gt;" is itself the escaped form of "<script>", so it is only checked via decoding.
RAW_CHECKED_PAYLOADS = tuple(p for p in PAYLOADS if not p.startswith("&"))


# --------------------------------------------------------------------------- HTML helpers


class _Element:
    def __init__(self, tag: str, attrs: dict[str, str | None], ancestors: list["_Element"]):
        self.tag = tag
        self.attrs = attrs
        self.ancestors = ancestors

    def ancestor_classes(self) -> set[str]:
        classes: set[str] = set()
        for element in self.ancestors:
            classes.update((element.attrs.get("class") or "").split())
        return classes


class _Collector(HTMLParser):
    """Collects start tags (with ancestors) and the decoded text of ``<pre class="ocr-text">``."""

    VOID = {"input", "img", "meta", "link", "br", "hr", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[_Element] = []
        self._stack: list[_Element] = []
        self.pre_text: list[str] | None = None
        self._in_pre = False

    def handle_starttag(self, tag, attrs):
        element = _Element(tag, dict(attrs), list(self._stack))
        self.elements.append(element)
        if tag == "pre" and "ocr-text" in (element.attrs.get("class") or "").split():
            self._in_pre = True
            self.pre_text = []
        if tag not in self.VOID:
            self._stack.append(element)

    def handle_endtag(self, tag):
        if tag == "pre":
            self._in_pre = False
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break

    def handle_data(self, data):
        if self._in_pre and self.pre_text is not None:
            self.pre_text.append(data)


def parse(html: str) -> _Collector:
    collector = _Collector()
    collector.feed(html)
    collector.close()
    return collector


# --------------------------------------------------------------------------- strategies

_SPECIAL_TEXT = st.text(
    alphabet=st.sampled_from(list("abcXYZ019 <>&\"'/=;#")), min_size=1, max_size=20
)
_FRAGMENTS = st.one_of(st.sampled_from(PAYLOADS), noise_words(), _SPECIAL_TEXT)


def _fmt_amount(value) -> str:
    return f"{value:.2f}".replace(".", ",")


def _fmt_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


@st.composite
def ocr_texts(draw: st.DrawFn) -> str:
    """Texto_OCR mixing invoice-like lines with HTML special characters and payloads."""
    lines: list[str] = []
    supplier = " ".join(draw(st.lists(_FRAGMENTS, min_size=1, max_size=3)))
    lines.append(supplier)
    if draw(st.booleans()):
        lines.append("Factura nº " + draw(st.sampled_from(["F-2025-001", "A<b>1", "X&1", "7'\"8"])))
    if draw(st.booleans()):
        lines.append("Fecha: " + _fmt_date(draw(dates(date(2000, 1, 1)))))
    if draw(st.booleans()):
        lines.append("NIF " + draw(valid_tax_ids()))
    if draw(st.booleans()):
        lines.append("Base imponible " + _fmt_amount(draw(amounts())) + " €")
    if draw(st.booleans()):
        lines.append("IVA 21% " + _fmt_amount(draw(amounts())) + " €")
    if draw(st.booleans()):
        lines.append("Total " + _fmt_amount(draw(amounts())) + " €")
    lines.extend(draw(st.lists(_FRAGMENTS, max_size=3)))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- fixtures


class _Env:
    def __init__(self, flask_app: Flask, ocr: FakeOcrEngine):
        self.app = flask_app
        self.ocr = ocr
        self.client = flask_app.test_client()

    def review_html(self, text: str) -> str:
        self.ocr.text = text
        response = self.client.post(
            "/uploads",
            data={"file": (io.BytesIO(JPEG), "factura.jpg")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 200
        return response.get_data(as_text=True)


@pytest.fixture(scope="module")
def review_env() -> Iterator[_Env]:
    """One app per module (Hypothesis must not reuse function-scoped fixtures across examples).

    Each example uploads a new Borrador with a fresh uuid, so examples do not interfere.
    """
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(db_path=root / "db" / "invoices.db", images_dir=root / "images")
        ocr = FakeOcrEngine()

        def fixed_clock() -> datetime:
            return FIXED_NOW

        yield _Env(create_app(settings, ocr, clock=fixed_clock), ocr)


# --------------------------------------------------------------------------- property


# Feature: invoice-reader, Property 9: El formulario de revisión refleja el Borrador y escapa HTML
@hyp_settings(max_examples=100, deadline=None)
@given(text=ocr_texts())
def test_review_form_reflects_draft_and_escapes_html(review_env: _Env, text: str):
    """**Validates: Requirements 4.1, 4.3, 14.7**"""
    draft = extract(text) if text.strip() else empty_draft()

    html = review_env.review_html(text)
    page = parse(html)

    # Each Campo_Apunte shows the Borrador value (decoded by the parser) and its escaped form.
    for name in FIELDS:
        controls = [e for e in page.elements if e.attrs.get("name") == name]
        assert len(controls) == 1, name
        control = controls[0]
        value = draft.values[name]
        if control.tag == "select":
            selected = [
                e.attrs.get("value")
                for e in page.elements
                if e.tag == "option" and control in e.ancestors and "selected" in e.attrs
            ]
            assert selected == [value]
        else:
            assert control.attrs.get("value") == value, name
            assert f'value="{escape(value)}"' in html, name

        # field--missing and "No detectado" exactly for the fields in ``missing``.
        is_missing = name in draft.missing
        assert ("field--missing" in control.ancestor_classes()) == is_missing, name
        note = next((e for e in page.elements if e.attrs.get("id") == f"f-{name}-missing"), None)
        assert (note is not None) == is_missing, name

    # The Texto_OCR is shown escaped inside <pre class="ocr-text"> and decodes to the original.
    assert page.pre_text is not None
    assert "".join(page.pre_text) == text
    assert str(escape(text)) in html

    # No payload with special characters appears raw, and no injected element is parsed.
    for payload in RAW_CHECKED_PAYLOADS:
        if payload in text:
            assert payload not in html, payload
    assert not any(e.tag == "script" and not e.attrs.get("src") for e in page.elements)
    assert not any(e.tag == "b" for e in page.elements)
    assert not any("onerror" in e.attrs or "onmouseover" in e.attrs for e in page.elements)
