"""Utilidades puras de presentación para la Portada y las plantillas.

- ``MONTH_NAMES_ES``: nombres de los meses en español, de Enero a Diciembre.
- ``MAX_RECENT_PAGE``: número máximo de Página_Recientes aceptado.
- ``format_eur``: formatea un Importe en formato español (filtro Jinja ``eur``).
- ``parse_recent_page``: normaliza el número de Página_Recientes recibido en la URL.

No se usa ``locale``: el contenedor no tiene *locales* instalados y el resultado
debe ser determinista.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MONTH_NAMES_ES: tuple[str, ...] = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)

MAX_RECENT_PAGE = 100_000

_CENT = Decimal("0.01")
_MAX_PAGE_DIGITS = len(str(MAX_RECENT_PAGE))


def format_eur(value: Decimal | int | None) -> str:
    """Formatea un Importe como ``"1.234,56 €"`` (Req. 16.18).

    Cuantiza a dos decimales con ``ROUND_HALF_UP``, agrupa los miles con ``.``,
    usa ``,`` como separador decimal y añade ``" €"`` con un espacio normal.
    Los valores negativos se admiten por robustez con el signo delante
    (``"-1.234,50 €"``). ``None`` devuelve ``""``.

    Raises:
        ValueError: si el valor no es finito (``NaN`` o infinito).
    """
    if value is None:
        return ""
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if not amount.is_finite():
        raise ValueError(f"Importe no finito: {value!r}")
    quantized = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    sign = "-" if quantized < 0 else ""
    # Formato con coma de miles y punto decimal; luego se intercambian.
    us_style = f"{abs(quantized):,.2f}"
    spanish = us_style.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{sign}{spanish} €"


def parse_recent_page(raw: str | None) -> int:
    """Normaliza el número de Página_Recientes (Req. 16.15).

    Solo acepta cadenas de dígitos ASCII cuyo valor esté entre 1 y
    ``MAX_RECENT_PAGE``; cualquier otra entrada devuelve 1.
    """
    if not isinstance(raw, str) or not raw or not (raw.isascii() and raw.isdecimal()):
        return 1
    significant = raw.lstrip("0")
    # Evita convertir cadenas enormes (límite de dígitos de int) y valores fuera de rango.
    if not significant or len(significant) > _MAX_PAGE_DIGITS:
        return 1
    page = int(significant)
    return page if page <= MAX_RECENT_PAGE else 1
