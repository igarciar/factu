"""Unit tests for tools/check_coverage.py (requirement 15.3)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_coverage.py"

_spec = importlib.util.spec_from_file_location("check_coverage", SCRIPT)
check_coverage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_coverage)


def _report(percents: dict[str, float]) -> dict:
    return {
        "meta": {"version": "7.x"},
        "files": {
            path: {"summary": {"percent_covered": pct}} for path, pct in percents.items()
        },
        "totals": {"percent_covered": 90.0},
    }


def _write(tmp_path: Path, content) -> str:
    path = tmp_path / "coverage.json"
    text = content if isinstance(content, str) else json.dumps(content)
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_all_files_at_or_above_minimum_returns_0(tmp_path, capsys):
    report = _write(tmp_path, _report({"app/config.py": 80.0, "app/models.py": 100.0}))

    assert check_coverage.main([report, "--min", "80"]) == 0
    assert "All files" in capsys.readouterr().out


def test_one_file_below_minimum_returns_1_and_prints_name(tmp_path, capsys):
    report = _write(tmp_path, _report({"app/config.py": 95.0, "app/uploads.py": 79.99}))

    assert check_coverage.main([report, "--min", "80"]) == 1
    out = capsys.readouterr().out
    assert "app/uploads.py" in out
    assert "79.99%" in out
    assert "app/config.py" not in out


def test_files_outside_app_are_ignored(tmp_path):
    report = _write(
        tmp_path,
        _report({"app/config.py": 90.0, "tools/check_coverage.py": 0.0, "tests/x.py": 10.0}),
    )

    assert check_coverage.main([report, "--min", "80"]) == 0


def test_windows_backslash_paths_are_recognised(tmp_path, capsys):
    report = _write(
        tmp_path,
        _report({"app\\routes\\invoices.py": 50.0, "tools\\check_coverage.py": 0.0}),
    )

    assert check_coverage.main([report, "--min", "80"]) == 1
    out = capsys.readouterr().out
    assert "app/routes/invoices.py" in out
    assert "tools" not in out


def test_leading_dot_slash_is_normalised():
    assert check_coverage.is_app_file(".\\app\\tax_id.py")
    assert check_coverage.is_app_file("./app/tax_id.py")
    assert not check_coverage.is_app_file("application/x.py")


def test_min_defaults_to_80(tmp_path):
    report = _write(tmp_path, _report({"app/config.py": 79.0}))

    assert check_coverage.main([report]) == 1


def test_missing_report_returns_2(tmp_path, capsys):
    assert check_coverage.main([str(tmp_path / "nope.json")]) == 2
    assert "not found" in capsys.readouterr().err


def test_invalid_json_returns_2(tmp_path, capsys):
    report = _write(tmp_path, "{not json")

    assert check_coverage.main([report]) == 2
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    [
        {"totals": {}},
        [],
        {"files": {"app/config.py": {"summary": {}}}},
        {"files": {"app/config.py": None}},
    ],
)
def test_unexpected_shape_returns_2(tmp_path, capsys, content):
    report = _write(tmp_path, content)

    assert check_coverage.main([report]) == 2
    assert "invalid coverage report" in capsys.readouterr().err


def test_cli_exit_code(tmp_path):
    report = _write(tmp_path, _report({"app/config.py": 10.0}))

    result = subprocess.run(
        [sys.executable, str(SCRIPT), report, "--min", "80"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "app/config.py" in result.stdout
