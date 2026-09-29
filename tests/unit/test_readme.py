"""README.md documents the lack of authentication and the port-exposure risk (Req. 14.2)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[2] / "README.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README.read_text(encoding="utf-8").lower()


def test_readme_warns_there_is_no_authentication(readme_text: str) -> None:
    assert "sin autenticación" in readme_text or "no tiene autenticación" in readme_text


def test_readme_warns_not_to_expose_the_port(readme_text: str) -> None:
    assert re.search(r"no redirijas el puerto[^\n]*router", readme_text)
    assert re.search(r"no[^\n]*expon[^\n]*internet", readme_text)


def test_readme_explains_bind_address(readme_text: str) -> None:
    assert "bind_address" in readme_text
