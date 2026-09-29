"""Unit tests for app.uploads (Req. 1.2, 1.4, 1.5, 1.6, 14.5)."""

import pytest

from app.uploads import (
    JPEG_MAGIC,
    PNG_MAGIC,
    FileTooLarge,
    MissingFile,
    UnsupportedFormat,
    UploadError,
    detect_format,
    validate_upload,
)

MAX = 100


def _jpeg(size: int) -> bytes:
    return JPEG_MAGIC + b"\x00" * (size - len(JPEG_MAGIC))


def _png(size: int) -> bytes:
    return PNG_MAGIC + b"\x00" * (size - len(PNG_MAGIC))


@pytest.mark.parametrize("exc", [MissingFile, UnsupportedFormat, FileTooLarge])
def test_errors_share_base_class(exc):
    assert issubclass(exc, UploadError)


def test_detect_format_jpeg_and_png():
    assert detect_format(_jpeg(10)) == "jpg"
    assert detect_format(_png(10)) == "png"


@pytest.mark.parametrize(
    "data",
    [b"", b"GIF89a", PNG_MAGIC[:-1], JPEG_MAGIC[:2], b"%PDF-1.7"],
)
def test_detect_format_unknown_returns_none(data):
    assert detect_format(data) is None


@pytest.mark.parametrize("data", [None, b""])
def test_missing_or_empty_file(data):
    with pytest.raises(MissingFile):
        validate_upload(data, MAX)


def test_size_equal_to_max_is_accepted():
    assert validate_upload(_jpeg(MAX), MAX) == "jpg"
    assert validate_upload(_png(MAX), MAX) == "png"


def test_size_max_plus_one_is_rejected():
    with pytest.raises(FileTooLarge):
        validate_upload(_png(MAX + 1), MAX)


def test_size_checked_before_signature():
    # Oversized garbage must report size, not format.
    with pytest.raises(FileTooLarge):
        validate_upload(b"x" * (MAX + 1), MAX)


def test_truncated_png_signature_is_unsupported():
    with pytest.raises(UnsupportedFormat):
        validate_upload(PNG_MAGIC[:-1], MAX)


def test_unknown_signature_is_unsupported():
    with pytest.raises(UnsupportedFormat):
        validate_upload(b"GIF89a" + b"\x00" * 10, MAX)


def test_valid_small_files():
    assert validate_upload(JPEG_MAGIC, MAX) == "jpg"
    assert validate_upload(PNG_MAGIC, MAX) == "png"
