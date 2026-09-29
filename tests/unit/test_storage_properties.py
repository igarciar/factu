"""Pruebas de propiedades de ``app.storage.ImageStore`` (Req. 4.6)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from app.storage import ImageStore

TTL = timedelta(hours=24)
TTL_SECONDS = int(TTL.total_seconds())

# Instantes en segundos enteros (2001–2090): la resolución de mtime en NTFS es de 100 ns,
# así que los segundos enteros se conservan exactos y la frontera ``== 24 h`` es precisa.
_NOW_MIN = int(datetime(2001, 1, 1, tzinfo=timezone.utc).timestamp())
_NOW_MAX = int(datetime(2090, 1, 1, tzinfo=timezone.utc).timestamp())

# Antigüedad en segundos: arbitraria (incluye mtime en el futuro) y valores en la frontera.
ages = st.one_of(
    st.integers(min_value=-TTL_SECONDS, max_value=4 * TTL_SECONDS),
    st.sampled_from([TTL_SECONDS - 1, TTL_SECONDS, TTL_SECONDS + 1, 0]),
)

tmp_names = st.tuples(
    st.uuids().map(lambda u: u.hex),
    st.sampled_from(["jpg", "png", "txt", "jpg.part", "png.part", "txt.part"]),
).map(lambda pair: f"{pair[0]}.{pair[1]}")

root_names = st.tuples(
    st.uuids().map(lambda u: u.hex),
    st.sampled_from(["jpg", "png"]),
).map(lambda pair: f"{pair[0]}.{pair[1]}")


def _write(path: Path, mtime: int) -> None:
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))


# Feature: invoice-reader, Property 10: Purga de Borradores caducados
@settings(max_examples=100)
@given(
    now_ts=st.integers(min_value=_NOW_MIN, max_value=_NOW_MAX),
    tmp_files=st.dictionaries(tmp_names, ages, max_size=12),
    root_files=st.dictionaries(root_names, ages, max_size=4),
)
def test_purge_removes_exactly_files_older_than_ttl(
    now_ts: int, tmp_files: dict[str, int], root_files: dict[str, int]
) -> None:
    """**Validates: Requirements 4.6**

    ``purge_expired_drafts(now, 24h)`` elimina exactamente los ficheros de ``tmp/`` con
    antigüedad ``> 24 h``, conserva el resto y no toca las imágenes de Apuntes.
    """
    now = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = ImageStore(Path(tmp_dir) / "images")
        for name, age in tmp_files.items():
            _write(store.tmp / name, now_ts - age)
        for name, age in root_files.items():
            _write(store.root / name, now_ts - age)

        removed = store.purge_expired_drafts(now, TTL)

        expired = {name for name, age in tmp_files.items() if age > TTL_SECONDS}
        assert sorted(removed) == sorted(expired)
        assert len(removed) == len(set(removed))
        remaining_tmp = {p.name for p in store.tmp.iterdir()}
        assert remaining_tmp == set(tmp_files) - expired
        remaining_root = {p.name for p in store.root.iterdir() if p.is_file()}
        assert remaining_root == set(root_files)
