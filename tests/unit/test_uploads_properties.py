"""Pruebas de propiedades de ``app.uploads`` (Property 1).

El oráculo clasifica los bytes con las firmas de ``tests.strategies`` (no con
las de ``app.uploads``) y sigue el orden del diseño: vacío → tamaño → firma.
"""

from __future__ import annotations

import inspect

from hypothesis import given, settings
from hypothesis import strategies as st

from app.uploads import FileTooLarge, MissingFile, UnsupportedFormat, validate_upload
from tests.strategies import JPEG_SIGNATURE, PNG_SIGNATURE, jpeg_bytes, png_bytes

# Nombres y content-types del cliente, a menudo engañosos respecto al contenido.
client_filenames = st.one_of(
    st.sampled_from(["foto.jpg", "scan.PNG", "factura.pdf", "../../etc/passwd", "x", "a.jpeg.png", ""]),
    st.text(max_size=40),
)
client_content_types = st.sampled_from(
    ["image/jpeg", "image/png", "application/pdf", "text/plain", "application/octet-stream", ""]
)


@st.composite
def _mutated_signature(draw: st.DrawFn) -> bytes:
    """Firma con un byte cambiado seguida de cola arbitraria (no es JPEG ni PNG)."""
    sig = bytearray(draw(st.sampled_from([JPEG_SIGNATURE, PNG_SIGNATURE])))
    index = draw(st.integers(min_value=0, max_value=len(sig) - 1))
    sig[index] = draw(st.integers(min_value=0, max_value=255).filter(lambda b: b != sig[index]))
    return bytes(sig) + draw(st.binary(max_size=64))


upload_data = st.one_of(
    st.none(),
    st.just(b""),
    st.binary(max_size=300),
    jpeg_bytes(),
    png_bytes(),
    # Prefijos truncados de una firma: casi JPEG/PNG, pero no.
    st.sampled_from([JPEG_SIGNATURE, PNG_SIGNATURE]).flatmap(
        lambda sig: st.integers(min_value=1, max_value=len(sig) - 1).map(lambda n: sig[:n])
    ),
    _mutated_signature(),
)


@st.composite
def data_and_max_bytes(draw: st.DrawFn) -> tuple[bytes | None, int]:
    """``(data, max_bytes)`` con ``max_bytes > 0``, a menudo en el límite ``len(data)``."""
    data = draw(upload_data)
    size = len(data) if data else 0
    boundary = [n for n in (size - 1, size, size + 1) if n > 0]
    max_bytes = draw(st.one_of(st.integers(min_value=1, max_value=600), st.sampled_from(boundary or [1])))
    return data, max_bytes


def _expected(data: bytes | None, max_bytes: int) -> str | type[Exception]:
    """Oráculo independiente de la clasificación de una subida."""
    if data is None or len(data) == 0:
        return MissingFile
    if len(data) > max_bytes:
        return FileTooLarge
    if data[: len(JPEG_SIGNATURE)] == JPEG_SIGNATURE:
        return "jpg"
    if data[: len(PNG_SIGNATURE)] == PNG_SIGNATURE:
        return "png"
    return UnsupportedFormat


def test_validate_upload_has_no_filename_or_content_type_parameter() -> None:
    """El nombre y el content-type del cliente no pueden influir: no se reciben (14.5)."""
    assert list(inspect.signature(validate_upload).parameters) == ["data", "max_bytes"]


# Feature: invoice-reader, Property 1: Clasificación de subidas por firma y tamaño
@settings(max_examples=200)
@given(case=data_and_max_bytes(), filename=client_filenames, content_type=client_content_types)
def test_upload_classification_by_signature_and_size(
    case: tuple[bytes | None, int], filename: str, content_type: str
) -> None:
    """**Validates: Requirements 1.2, 1.4, 1.5, 14.5**

    ``validate_upload`` devuelve ``"jpg"``/``"png"`` si y solo si
    ``0 < len(data) <= max_bytes`` y ``data`` empieza por la firma
    correspondiente; en otro caso lanza ``MissingFile``, ``FileTooLarge`` o
    ``UnsupportedFormat``. El nombre y el content-type del cliente (generados
    y a menudo contradictorios) no forman parte de la entrada.
    """
    data, max_bytes = case
    expected = _expected(data, max_bytes)

    try:
        result: str | type[Exception] = validate_upload(data, max_bytes)
    except (MissingFile, FileTooLarge, UnsupportedFormat) as exc:
        result = type(exc)

    assert result == expected, (
        f"data={data!r} max_bytes={max_bytes} filename={filename!r} content_type={content_type!r}"
    )
