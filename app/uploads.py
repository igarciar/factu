"""Validation of uploaded invoice images.

The format is detected only from the file's binary signature; the client's
``filename`` and ``content_type`` are never consulted (Req. 14.5).
"""

from __future__ import annotations

from typing import Literal

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class UploadError(Exception):
    """Base class for every upload validation error."""


class MissingFile(UploadError):
    """No file was sent, or the file is empty (Req. 1.6)."""


class UnsupportedFormat(UploadError):
    """The content is neither JPEG nor PNG by signature (Req. 1.4)."""


class FileTooLarge(UploadError):
    """The file exceeds the configured maximum size (Req. 1.5)."""


def detect_format(data: bytes) -> Literal["jpg", "png"] | None:
    """Return ``"jpg"`` or ``"png"`` from the leading signature, else ``None``."""
    if data.startswith(JPEG_MAGIC):
        return "jpg"
    if data.startswith(PNG_MAGIC):
        return "png"
    return None


def validate_upload(data: bytes | None, max_bytes: int) -> Literal["jpg", "png"]:
    """Validate an uploaded file and return its detected format.

    Checks run in this order: missing/empty -> size -> signature.

    Raises:
        MissingFile: ``data`` is ``None`` or empty.
        FileTooLarge: ``len(data) > max_bytes``.
        UnsupportedFormat: the signature is neither JPEG nor PNG.
    """
    if not data:
        raise MissingFile("No file received")
    if len(data) > max_bytes:
        raise FileTooLarge(f"File exceeds {max_bytes} bytes")
    fmt = detect_format(data)
    if fmt is None:
        raise UnsupportedFormat("Only JPEG and PNG are supported")
    return fmt
