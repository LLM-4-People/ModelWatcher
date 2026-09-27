"""Test: no Python module refers to a name it never defines or imports.

Catches finding F66: routes.handle_client_error() read a bare `c` that routes.py never
imported, so the browser's error reporter got a 500 for every report since v1.0.0.
Nothing ran that line in the test suite; pyflakes finds it without running it.
"""
import ast

import pytest
from pyflakes import checker, messages

from backend.state import BACKEND_DIR, BASE_DIR

_UNDEFINED = (messages.UndefinedName, messages.UndefinedLocal, messages.UndefinedExport)
_SOURCES = sorted([*BACKEND_DIR.glob("*.py"), *(BASE_DIR / "scripts").rglob("*.py")])


@pytest.mark.parametrize("path", _SOURCES, ids=lambda p: p.relative_to(BASE_DIR).as_posix())
def test_no_undefined_names(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    found = [m for m in checker.Checker(tree, filename=str(path)).messages if isinstance(m, _UNDEFINED)]
    assert not found, "\n".join(str(m) for m in found)
