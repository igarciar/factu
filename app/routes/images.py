"""Blueprint ``images``: serves Borrador and Apunte images from the Almacén_Imágenes.

- ``GET /drafts/<draft_id>/image``: the ``draft_id`` must match ``DRAFT_ID``; the files are
  loaded with ``DraftService.load_files`` (expired Borradores count as absent, Req. 4.6).
- ``GET /entries/<int:entry_id>/image``: the Apunte is read with ``EntryService.get`` and its
  ``image_filename`` is resolved with ``ImageStore.resolve`` (Req. 8.5).

Images are only reachable through an Apunte id or a generated draft id, never through a
client-supplied path; every path is checked to stay inside the store (Req. 14.3, 14.4).
Anything else answers ``abort(404)`` (Req. 8.6). ``Content-Type`` comes from the stored
extension (``jpg`` → ``image/jpeg``, ``png`` → ``image/png``) and ``nosniff`` is added by
``app.security``. No ``Content-Disposition`` is sent, so file names and paths never leak.

Requirements: 4.2, 8.5, 8.6, 14.3, 14.4.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from flask import Blueprint, Response, abort, current_app, send_file

from app.storage import DRAFT_ID

bp = Blueprint("images", __name__)

MIMETYPES: Mapping[str, str] = MappingProxyType({"jpg": "image/jpeg", "png": "image/png"})

#: Largest value SQLite accepts as INTEGER; bigger ids cannot exist (and would overflow).
MAX_ENTRY_ID = 2**63 - 1

#: Borradores are temporary and can be cancelled: never keep them in any cache.
DRAFT_CACHE_CONTROL = "no-store"


def _services() -> dict[str, Any]:
    return current_app.extensions["invoice"]


def _mimetype(path: Path) -> str:
    mimetype = MIMETYPES.get(path.suffix.lower().lstrip("."))
    if mimetype is None:
        abort(404)
    return mimetype


def _send_image(path: Path) -> Response:
    """``send_file`` with the ``Content-Type`` of the extension and no file name exposed.

    Werkzeug adds ``Content-Disposition: inline; filename=<name>`` by default; it is removed
    so the stored (generated) name never reaches the client.
    """
    response = send_file(path, mimetype=_mimetype(path))
    response.headers.pop("Content-Disposition", None)
    return response


def _inside(path: Path, root: Path) -> Path | None:
    """``path`` resolved, if it is an existing regular file inside ``root``; else ``None``."""
    try:
        candidate = Path(path).resolve()
        if not candidate.is_relative_to(Path(root).resolve()) or not candidate.is_file():
            return None
    except (OSError, ValueError):
        return None
    return candidate


@bp.get("/drafts/<draft_id>/image")
def draft_image(draft_id: str) -> Response:
    """Temporary image of a Borrador under review (Req. 4.2)."""
    if DRAFT_ID.fullmatch(draft_id) is None:
        abort(404)
    services = _services()
    files = services["drafts"].load_files(draft_id)
    if files is None:
        abort(404)
    path = _inside(files.image_path, services["store"].tmp)
    if path is None:
        abort(404)
    response = _send_image(path)
    response.headers["Cache-Control"] = DRAFT_CACHE_CONTROL
    return response


@bp.get("/entries/<int:entry_id>/image")
def entry_image(entry_id: int) -> Response:
    """Image of a saved Apunte (Req. 8.5); 404 if the Apunte or its file is missing (8.6)."""
    if entry_id > MAX_ENTRY_ID:
        abort(404)
    services = _services()
    entry = services["entries"].get(entry_id)
    if entry is None:
        abort(404)
    path = services["store"].resolve(entry.image_filename)
    if path is None:
        abort(404)
    # send_file adds ETag/Last-Modified and "no-cache": the browser revalidates, so an edited
    # or deleted Apunte never shows a stale image. "private" keeps it out of shared caches.
    response = _send_image(path)
    response.cache_control.private = True
    return response
