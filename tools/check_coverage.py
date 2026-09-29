"""Per-file coverage gate for the ``app/`` package (requirement 15.3).

Usage::

    python tools/check_coverage.py coverage.json --min 80

Reads the JSON report produced by ``pytest --cov-report=json`` and checks
``files[*].summary.percent_covered`` for every file under ``app/``. Paths are
normalised so both ``app\\x.py`` (Windows host) and ``app/x.py`` (Linux
container) are recognised.

Exit codes:
    0  every ``app/`` file reaches the minimum
    1  at least one ``app/`` file is below the minimum (listed on stdout)
    2  the report is missing, is not valid JSON or has an unexpected shape
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

EXIT_OK = 0
EXIT_BELOW_MIN = 1
EXIT_BAD_REPORT = 2

APP_PREFIX = "app/"


def normalize_path(path: str) -> str:
    """Return ``path`` with ``/`` separators and without a leading ``./``."""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_app_file(path: str) -> bool:
    """True if ``path`` (relative to the project root) lives under ``app/``."""
    return normalize_path(path).startswith(APP_PREFIX)


def find_below_minimum(report: dict, minimum: float) -> list[tuple[str, float]]:
    """Return ``(path, percent)`` for each ``app/`` file below ``minimum``.

    Raises ``ValueError`` if the report does not have the expected shape.
    """
    files = report.get("files") if isinstance(report, dict) else None
    if not isinstance(files, dict):
        raise ValueError("report has no 'files' object")

    below: list[tuple[str, float]] = []
    for path, data in files.items():
        if not is_app_file(path):
            continue
        try:
            percent = float(data["summary"]["percent_covered"])
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError(f"missing summary.percent_covered for {path}") from exc
        if percent < minimum:
            below.append((normalize_path(path), percent))
    return sorted(below)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail if any app/ file is below the minimum coverage."
    )
    parser.add_argument("report", help="path to coverage.json")
    parser.add_argument(
        "--min",
        dest="minimum",
        type=float,
        default=80.0,
        help="minimum percent_covered per file (default: 80)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the coverage gate and return the process exit code."""
    args = _parse_args(argv)

    try:
        with open(args.report, encoding="utf-8") as fh:
            report = json.load(fh)
    except FileNotFoundError:
        print(f"error: coverage report not found: {args.report}", file=sys.stderr)
        return EXIT_BAD_REPORT
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read coverage report {args.report}: {exc}", file=sys.stderr)
        return EXIT_BAD_REPORT

    try:
        below = find_below_minimum(report, args.minimum)
    except ValueError as exc:
        print(f"error: invalid coverage report {args.report}: {exc}", file=sys.stderr)
        return EXIT_BAD_REPORT

    if below:
        print(f"Files in {APP_PREFIX} below {args.minimum:g}% coverage:")
        for path, percent in below:
            print(f"  {path}: {percent:.2f}%")
        return EXIT_BELOW_MIN

    print(f"All files in {APP_PREFIX} reach at least {args.minimum:g}% coverage.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
