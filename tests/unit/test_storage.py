"""Unit tests for app.storage.ImageStore (Req. 4.5, 4.6, 6.2, 6.3, 9.4, 14.3, 14.4, 14.5)."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import storage
from app.storage import DRAFT_ID, UUID_NAME, ImageStore
from app.uploads import JPEG_MAGIC, PNG_MAGIC

DRAFT = "0123456789abcdef0123456789abcdef"
OTHER_DRAFT = "fedcba9876543210fedcba9876543210"
JPEG = JPEG_MAGIC + bytes(range(256)) + b"\r\n\x00\n\r"
PNG = PNG_MAGIC + b"\x00\xff\r\n" * 16
NOW = datetime(2025, 3, 2, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(hours=24)


@pytest.fixture
def store(tmp_path: Path) -> ImageStore:
    return ImageStore(tmp_path / "images")


def _set_mtime(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def test_init_creates_root_and_tmp(tmp_path: Path):
    s = ImageStore(tmp_path / "a" / "images")
    assert s.root.is_dir() and s.tmp.is_dir()
    assert s.tmp == s.root / "tmp"
    ImageStore(tmp_path / "a" / "images")  # idempotent


@pytest.mark.parametrize(
    "pattern, ok, bad",
    [
        (UUID_NAME, DRAFT + ".jpg", DRAFT + ".gif"),
        (DRAFT_ID, DRAFT, DRAFT.upper()),
    ],
)
def test_patterns(pattern, ok, bad):
    assert pattern.match(ok)
    assert not pattern.match(bad)


# -- save_temp / save_temp_text / load_draft -------------------------------------------------


@pytest.mark.parametrize("data, ext", [(JPEG, "jpg"), (PNG, "png")])
def test_save_temp_keeps_bytes_identical(store: ImageStore, data: bytes, ext: str):
    path = store.save_temp(DRAFT, data, ext)
    assert path == store.tmp / f"{DRAFT}.{ext}"
    assert path.read_bytes() == data
    assert not list(store.tmp.glob("*.part"))


@pytest.mark.parametrize("draft_id, ext", [("../x", "jpg"), (DRAFT + "\n", "jpg"), (DRAFT, "gif"), (DRAFT, "../jpg")])
def test_save_temp_rejects_bad_input(store: ImageStore, draft_id: str, ext: str):
    with pytest.raises(ValueError):
        store.save_temp(draft_id, JPEG, ext)
    assert list(store.tmp.iterdir()) == []


def test_save_temp_removes_part_file_on_failure(store: ImageStore, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", boom)
    with pytest.raises(OSError):
        store.save_temp(DRAFT, JPEG, "jpg")
    assert list(store.tmp.iterdir()) == []


def test_save_temp_text_rejects_bad_id(store: ImageStore):
    with pytest.raises(ValueError):
        store.save_temp_text("nope", "x")


def test_load_draft_returns_files(store: ImageStore):
    text = "Línea 1\r\nTOTAL 12,34 €\n"
    store.save_temp(DRAFT, PNG, "png")
    store.save_temp_text(DRAFT, text)
    _set_mtime(store.tmp / f"{DRAFT}.png", NOW)

    files = store.load_draft(DRAFT)

    assert files is not None
    assert files.draft_id == DRAFT
    assert files.ext == "png"
    assert files.image_path.read_bytes() == PNG
    assert files.ocr_text == text
    assert files.modified_at == NOW


def test_load_draft_without_text_has_empty_ocr_text(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    files = store.load_draft(DRAFT)
    assert files is not None and files.ext == "jpg" and files.ocr_text == ""


@pytest.mark.parametrize("draft_id", [DRAFT, "../" + DRAFT, "", DRAFT + "\n"])
def test_load_draft_missing_or_invalid_is_none(store: ImageStore, draft_id: str):
    assert store.load_draft(draft_id) is None


def test_load_draft_ignores_directory_named_like_image(store: ImageStore):
    (store.tmp / f"{DRAFT}.jpg").mkdir()
    assert store.load_draft(DRAFT) is None


# -- promote / demote -------------------------------------------------------------------------


def test_promote_demote_round_trip(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    store.save_temp_text(DRAFT, "texto")

    final = store.promote(DRAFT)

    assert UUID_NAME.match(final) and final.endswith(".jpg")
    assert not final.startswith(DRAFT)  # fresh uuid4, not derived from the draft id
    assert (store.root / final).read_bytes() == JPEG
    assert store.load_draft(DRAFT) is None
    assert store.resolve(final) == (store.root / final).resolve()

    store.demote(final, DRAFT)

    assert not (store.root / final).exists()
    files = store.load_draft(DRAFT)
    assert files is not None and files.image_path.read_bytes() == JPEG and files.ocr_text == "texto"


def test_promote_generates_distinct_names(store: ImageStore):
    store.save_temp(DRAFT, PNG, "png")
    store.save_temp(OTHER_DRAFT, PNG, "png")
    assert store.promote(DRAFT) != store.promote(OTHER_DRAFT)


def test_promote_without_image_raises(store: ImageStore):
    with pytest.raises(FileNotFoundError):
        store.promote(DRAFT)


def test_promote_rejects_bad_id(store: ImageStore):
    with pytest.raises(ValueError):
        store.promote("..")


@pytest.mark.parametrize("final, draft_id", [("../x.jpg", DRAFT), (DRAFT + ".jpg", "..")])
def test_demote_rejects_bad_names(store: ImageStore, final: str, draft_id: str):
    with pytest.raises(ValueError):
        store.demote(final, draft_id)


# -- discard ---------------------------------------------------------------------------------


def test_discard_draft_removes_only_that_draft(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    store.save_temp_text(DRAFT, "a")
    (store.tmp / f"{DRAFT}.png.part").write_bytes(b"x")
    store.save_temp(OTHER_DRAFT, PNG, "png")

    store.discard_draft(DRAFT)
    store.discard_draft(DRAFT)  # idempotent
    store.discard_draft("../" + OTHER_DRAFT)  # invalid id: no-op

    assert sorted(p.name for p in store.tmp.iterdir()) == [f"{OTHER_DRAFT}.png"]


def test_discard_draft_text_keeps_image(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    store.save_temp_text(DRAFT, "a")

    store.discard_draft_text(DRAFT)
    store.discard_draft_text(DRAFT)
    store.discard_draft_text("bad")

    assert [p.name for p in store.tmp.iterdir()] == [f"{DRAFT}.jpg"]


# -- delete / resolve ------------------------------------------------------------------------


def test_delete(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    final = store.promote(DRAFT)

    assert store.delete(final) is True
    assert not (store.root / final).exists()
    assert store.delete(final) is False  # already gone (Req. 9.4)
    assert store.delete("../" + final) is False


@pytest.mark.parametrize(
    "name",
    [
        "",
        "..",
        "../" + DRAFT + ".jpg",
        "..\\" + DRAFT + ".jpg",
        "tmp\\" + DRAFT + ".jpg",
        "tmp/" + DRAFT + ".jpg",
        "%2e%2e/" + DRAFT + ".jpg",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "\\\\server\\share\\" + DRAFT + ".jpg",
        DRAFT + ".jpg\x00",
        DRAFT + "\x00.jpg",
        DRAFT + ".jpg\n",
        DRAFT.upper() + ".JPG",
        DRAFT + ".gif",
        DRAFT,
    ],
)
def test_resolve_rejects_malicious_names(store: ImageStore, name: str):
    assert store.resolve(name) is None


def test_resolve_rejects_absolute_path_to_existing_file(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    final = store.promote(DRAFT)
    assert store.resolve(str((store.root / final).resolve())) is None


def test_resolve_valid_but_missing_is_none(store: ImageStore):
    assert store.resolve(DRAFT + ".png") is None


def test_resolve_directory_is_none(store: ImageStore):
    (store.root / f"{DRAFT}.jpg").mkdir()
    assert store.resolve(f"{DRAFT}.jpg") is None


def test_resolve_does_not_see_temp_images(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    assert store.resolve(f"{DRAFT}.jpg") is None


def test_resolve_rejects_symlink_outside_root(store: ImageStore, tmp_path: Path):
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(JPEG)
    link = store.root / f"{DRAFT}.jpg"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert store.resolve(link.name) is None


# -- purge_expired_drafts --------------------------------------------------------------------


def test_purge_removes_only_expired_files(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    store.save_temp_text(DRAFT, "old")
    store.save_temp(OTHER_DRAFT, PNG, "png")
    store.save_temp_text(OTHER_DRAFT, "new")
    stale_part = store.tmp / "leftover.png.part"
    stale_part.write_bytes(b"x")
    (store.tmp / "subdir").mkdir()
    _set_mtime(store.tmp / "subdir", NOW - timedelta(days=10))

    _set_mtime(store.tmp / f"{DRAFT}.jpg", NOW - TTL - timedelta(seconds=1))
    _set_mtime(store.tmp / f"{DRAFT}.txt", NOW - TTL - timedelta(hours=5))
    _set_mtime(stale_part, NOW - timedelta(days=3))
    _set_mtime(store.tmp / f"{OTHER_DRAFT}.png", NOW - TTL + timedelta(seconds=1))
    _set_mtime(store.tmp / f"{OTHER_DRAFT}.txt", NOW - TTL)  # exactly 24 h: kept
    final_img = store.root / f"{OTHER_DRAFT}.png"
    final_img.write_bytes(PNG)
    _set_mtime(final_img, NOW - timedelta(days=30))  # Apunte images are never purged

    removed = store.purge_expired_drafts(NOW, TTL)

    assert sorted(removed) == sorted([f"{DRAFT}.jpg", f"{DRAFT}.txt", "leftover.png.part"])
    assert sorted(p.name for p in store.tmp.iterdir()) == sorted(
        [f"{OTHER_DRAFT}.png", f"{OTHER_DRAFT}.txt", "subdir"]
    )
    assert final_img.exists()
    assert store.purge_expired_drafts(NOW, TTL) == []  # idempotent


def test_purge_accepts_naive_now(store: ImageStore):
    store.save_temp(DRAFT, JPEG, "jpg")
    naive_now = datetime.now()
    _set_mtime(store.tmp / f"{DRAFT}.jpg", naive_now - timedelta(days=2))
    assert store.purge_expired_drafts(naive_now, TTL) == [f"{DRAFT}.jpg"]


def test_purge_missing_tmp_dir_returns_empty(store: ImageStore):
    store.tmp.rmdir()
    assert store.purge_expired_drafts(NOW, TTL) == []


def test_purge_tolerates_file_removed_before_stat(store: ImageStore, monkeypatch):
    store.save_temp(DRAFT, JPEG, "jpg")
    store.save_temp(OTHER_DRAFT, PNG, "png")
    for p in store.tmp.iterdir():
        _set_mtime(p, NOW - timedelta(days=2))
    gone = store.tmp / f"{DRAFT}.jpg"
    real_stat = Path.stat

    def racing_stat(self, *args, **kwargs):
        if self == gone:
            raise FileNotFoundError(str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", racing_stat)
    assert store.purge_expired_drafts(NOW, TTL) == [f"{OTHER_DRAFT}.png"]


def test_purge_tolerates_file_removed_before_unlink(store: ImageStore, monkeypatch):
    store.save_temp(DRAFT, JPEG, "jpg")
    store.save_temp(OTHER_DRAFT, PNG, "png")
    for p in store.tmp.iterdir():
        _set_mtime(p, NOW - timedelta(days=2))
    victim = store.tmp / f"{DRAFT}.jpg"
    real_unlink = Path.unlink

    def racing_unlink(self, *args, **kwargs):
        if self == victim:
            real_unlink(self)  # another worker deletes it first
            raise FileNotFoundError(str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", racing_unlink)
    assert store.purge_expired_drafts(NOW, TTL) == [f"{OTHER_DRAFT}.png"]
    assert list(store.tmp.iterdir()) == []
