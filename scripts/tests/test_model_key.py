"""Test: model keys are built and split only by backend.state's make_model_key/parse_model_key.

Catches finding F45 (backend part): notifications.py built provider-level keys as
f"{provider}::", a second copy of MODEL_KEY_SEP that would drift if the separator changed.
The frontend half of F45 (its own '::' literals) is tracked separately.
"""
import ast

import pytest

import backend.state as st
from backend.state import BACKEND_DIR, BASE_DIR, MODEL_KEY_SEP

_SPLITTERS = {"split", "rsplit", "partition", "rpartition", "find", "rfind", "index", "rindex",
              "startswith", "endswith", "join", "replace"}


def _hand_built_keys(source: str) -> list[int]:
    """Lines that put the separator into an f-string or hand it to a str method."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.JoinedStr):
            if any(isinstance(v, ast.Constant) and MODEL_KEY_SEP in str(v.value) for v in node.values):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _SPLITTERS:
            if any(isinstance(a, ast.Constant) and a.value == MODEL_KEY_SEP for a in [node.func.value, *node.args]):
                lines.append(node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if any(isinstance(side, ast.Constant) and side.value == MODEL_KEY_SEP for side in (node.left, node.right)):
                lines.append(node.lineno)
    return lines


_SOURCES = sorted([*(p for p in BACKEND_DIR.glob("*.py") if p.name != "state.py"), *(BASE_DIR / "scripts" / "util").glob("*.py")])


@pytest.mark.parametrize("path", _SOURCES, ids=lambda p: p.relative_to(BASE_DIR).as_posix())
def test_no_hand_built_model_keys(path):
    assert not _hand_built_keys(path.read_text()), f"use make_model_key/parse_model_key in {path.name}"


@pytest.mark.parametrize("snippet, flagged", [
    ('k = f"{provider}::"', True),
    ('k = f"{p}::{m}"', True),
    ('p, _, m = key.partition("::")', True),
    ('k = provider + "::" + m', True),
    ('k = make_model_key(provider, "")', False),
    ('d = "Model key (`Provider::model_id`)"', False),
])
def test_scanner(snippet, flagged):
    assert bool(_hand_built_keys(snippet)) is flagged


def test_provider_level_key_round_trips():
    assert st.parse_model_key(st.make_model_key("Acme", "")) == ("Acme", "")
