"""Root pytest configuration: golden-file support (Architecture §0 rule 4, §13).

Usage in a test:

    def test_tokens(golden):
        golden("lexer/greenhouse.tokens", render_tokens(...))

* Goldens live under tests/golden/<name> and are compared as bytes.
* A missing golden is a failure unless --update-golden is given.
* --update-golden is refused when the CI environment variable is set.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "tests" / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite golden files from the current output",
    )


def _as_bytes(actual: str | bytes) -> bytes:
    if isinstance(actual, bytes):
        return actual
    assert "\r" not in actual, "generated text must not contain carriage returns"
    return actual.encode("utf-8")


def _visible(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace").replace("\r", "\\r")
    return text.splitlines(keepends=True)


@pytest.fixture
def golden(request: pytest.FixtureRequest):
    update = request.config.getoption("--update-golden")
    if update and os.environ.get("CI"):
        pytest.fail("--update-golden is refused when CI is set")

    def check(name: str, actual: str | bytes) -> None:
        path = GOLDEN_DIR / name
        data = _as_bytes(actual)
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            old = path.read_bytes() if path.exists() else None
            if old != data:
                path.write_bytes(data)
                print(f"UPDATED golden {name}")
            return
        if not path.exists():
            pytest.fail(f"missing golden {name}; run `py tasks.py golden` and review the diff")
        expected = path.read_bytes()
        if expected != data:
            diff = "".join(
                difflib.unified_diff(
                    _visible(expected), _visible(data), f"golden/{name}", "actual"
                )
            )
            pytest.fail(f"golden mismatch for {name}:\n{diff}")

    return check
