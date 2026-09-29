"""Image store: temporary Borrador files in ``root/tmp/`` and final Apunte images in ``root/``.

Layout (design.md, "Almacenamiento en disco")::

    root/{uuid32}.jpg|png          # Apunte images
    root/tmp/{draft_id}.jpg|png    # Borrador images (TTL 24 h)
    root/tmp/{draft_id}.txt        # Borrador Texto_OCR

Every name that reaches the file system is either generated here (``uuid4().hex``) or checked
against ``UUID_NAME``/``DRAFT_ID`` first, so client input can never build a path outside the
store (Req. 14.3, 14.4, 14.5). Image bytes are written as received and never transformed
(Req. 6.3).

Conventions: operations that write or move files raise ``ValueError`` for malformed ids;
lookups and clean-ups treat a malformed id as "nothing there" (``None``/``False``/no-op).

Requirements: 4.5, 4.6, 6.2, 6.3, 14.3, 14.4, 14.5.
"""

from __future__ import annotations

import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.models import DraftFiles, ImageExt

UUID_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png)$")
DRAFT_ID = re.compile(r"^[0-9a-f]{32}$")

IMAGE_EXTS: tuple[ImageExt, ...] = ("jpg", "png")
TEXT_EXT = "txt"
PART_SUFFIX = ".part"
TMP_DIRNAME = "tmp"


def _is_draft_id(value: object) -> bool:
    # fullmatch: "$" alone would also accept a trailing "\n".
    return isinstance(value, str) and DRAFT_ID.fullmatch(value) is not None


def _is_final_name(value: object) -> bool:
    return isinstance(value, str) and UUID_NAME.fullmatch(value) is not None


def _require_draft_id(draft_id: str) -> None:
    if not _is_draft_id(draft_id):
        raise ValueError(f"draft_id no válido: {draft_id!r}")


def _unlink_missing_ok(path: Path) -> bool:
    """Remove ``path``; return ``False`` if it did not exist (another worker may have won)."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` via ``target.part`` + ``fsync`` + ``os.replace``.

    The ``.part`` file is closed before ``os.replace`` because Windows cannot replace or
    delete a file that is still open.
    """
    part = target.with_name(target.name + PART_SUFFIX)
    try:
        with open(part, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(part, target)
    except BaseException:
        _unlink_missing_ok(part)
        raise


class ImageStore:
    """File-system store for invoice images and Borrador files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.tmp = self.root / TMP_DIRNAME
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)

    # -- Borradores -----------------------------------------------------------------------

    def _temp_image(self, draft_id: str, ext: str) -> Path:
        return self.tmp / f"{draft_id}.{ext}"

    def _temp_text(self, draft_id: str) -> Path:
        return self.tmp / f"{draft_id}.{TEXT_EXT}"

    def _find_temp_image(self, draft_id: str) -> tuple[Path, ImageExt, os.stat_result] | None:
        for ext in IMAGE_EXTS:
            path = self._temp_image(draft_id, ext)
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(st.st_mode):
                return path, ext, st
        return None

    def save_temp(self, draft_id: str, data: bytes, ext: str) -> Path:
        """Store the uploaded bytes unchanged as ``tmp/{draft_id}.{ext}`` (Req. 6.3, 14.5)."""
        _require_draft_id(draft_id)
        if ext not in IMAGE_EXTS:
            raise ValueError(f"extensión no válida: {ext!r}")
        target = self._temp_image(draft_id, ext)
        _atomic_write(target, bytes(data))
        return target

    def save_temp_text(self, draft_id: str, text: str) -> None:
        """Store the Texto_OCR as UTF-8 in ``tmp/{draft_id}.txt`` (no newline translation)."""
        _require_draft_id(draft_id)
        _atomic_write(self._temp_text(draft_id), text.encode("utf-8"))

    def load_draft(self, draft_id: str) -> DraftFiles | None:
        """Return the Borrador files, or ``None`` if the id is malformed or has no image.

        A missing text file yields ``ocr_text=""``. Expiry is decided by the caller from
        ``modified_at`` (UTC mtime of the image).
        """
        if not _is_draft_id(draft_id):
            return None
        found = self._find_temp_image(draft_id)
        if found is None:
            return None
        image_path, ext, st = found
        try:
            ocr_text = self._temp_text(draft_id).read_bytes().decode("utf-8", errors="replace")
        except FileNotFoundError:
            ocr_text = ""
        return DraftFiles(
            draft_id=draft_id,
            image_path=image_path,
            ext=ext,
            ocr_text=ocr_text,
            modified_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        )

    def promote(self, draft_id: str) -> str:
        """Move the Borrador image to ``root/{uuid4().hex}.{ext}`` and return that name (6.2).

        The name is always freshly generated, never derived from the draft id or the client.
        Raises ``FileNotFoundError`` if the Borrador has no image. The text file is kept.
        """
        _require_draft_id(draft_id)
        found = self._find_temp_image(draft_id)
        if found is None:
            raise FileNotFoundError(f"Borrador sin imagen: {draft_id}")
        source, ext, _ = found
        final_name = f"{uuid4().hex}.{ext}"
        os.replace(source, self.root / final_name)
        return final_name

    def demote(self, final_name: str, draft_id: str) -> None:
        """Undo ``promote``: move ``root/final_name`` back to ``tmp/{draft_id}.{ext}``."""
        if not _is_final_name(final_name):
            raise ValueError(f"nombre de imagen no válido: {final_name!r}")
        _require_draft_id(draft_id)
        ext = final_name.rsplit(".", 1)[1]
        os.replace(self.root / final_name, self._temp_image(draft_id, ext))

    def discard_draft(self, draft_id: str) -> None:
        """Delete every temporary file of the Borrador (Req. 4.5). Missing files are ignored."""
        if not _is_draft_id(draft_id):
            return
        for ext in (*IMAGE_EXTS, TEXT_EXT):
            path = self.tmp / f"{draft_id}.{ext}"
            _unlink_missing_ok(path)
            _unlink_missing_ok(path.with_name(path.name + PART_SUFFIX))

    def discard_draft_text(self, draft_id: str) -> None:
        """Delete only the Borrador text; used after a successful ``promote`` + commit."""
        if not _is_draft_id(draft_id):
            return
        _unlink_missing_ok(self._temp_text(draft_id))

    # -- Apunte images --------------------------------------------------------------------

    def delete(self, final_name: str) -> bool:
        """Delete ``root/final_name``; ``False`` if the name is invalid or the file was absent."""
        if not _is_final_name(final_name):
            return False
        return _unlink_missing_ok(self.root / final_name)

    def resolve(self, name: str) -> Path | None:
        """Return the absolute path of an existing Apunte image, or ``None`` (Req. 14.3, 14.4).

        ``name`` must match ``UUID_NAME``; the resolved path must stay inside ``root`` (this
        also rejects symlinks pointing elsewhere) and must be an existing regular file.
        """
        if not _is_final_name(name):
            return None
        root = self.root.resolve()
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return None
        return candidate

    # -- Purge ----------------------------------------------------------------------------

    def purge_expired_drafts(self, now: datetime, ttl: timedelta) -> list[str]:
        """Delete files in ``tmp/`` whose mtime is older than ``now - ttl`` (Req. 4.6).

        Safe to run concurrently from several workers: files that disappear between listing,
        ``stat()`` and ``unlink()`` are skipped. Returns only the names this call deleted.
        """
        # timestamp() handles both aware and naive (local time) datetimes.
        cutoff = (now - ttl).timestamp()
        try:
            names = sorted(os.listdir(self.tmp))
        except FileNotFoundError:
            return []
        removed: list[str] = []
        for name in names:
            path = self.tmp / name
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_mtime >= cutoff:
                continue
            if _unlink_missing_ok(path):
                removed.append(name)
        return removed
