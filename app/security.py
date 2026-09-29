"""Cabeceras de seguridad HTTP añadidas a todas las respuestas.

``register(app)`` instala un ``@app.after_request`` que añade ``SECURITY_HEADERS`` a
cualquier respuesta (páginas, errores, imágenes y ficheros estáticos) sin sobrescribir una
cabecera que la ruta ya haya fijado. La CSP solo permite recursos del propio origen, por eso
``htmx.min.js`` se sirve en local desde ``/static``.

Requirements: 14.5 (junto con el resto de controles del diseño, sección "security.py").
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from flask import Flask, Response

SECURITY_HEADERS: Mapping[str, str] = MappingProxyType(
    {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'self'; img-src 'self'",
    }
)


def add_security_headers(response: Response) -> Response:
    """Añade ``SECURITY_HEADERS`` a ``response`` conservando las que ya estén fijadas."""
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def register(app: Flask) -> None:
    """Registra ``add_security_headers`` como ``after_request`` de ``app``."""
    app.after_request(add_security_headers)
