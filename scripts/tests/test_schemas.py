"""Test: Pydantic body models match handler field expectations.

Catches bug family #5: openapi_extra duplicated handler field knowledge -
two places defining the same fields, guaranteed to drift.

Catches bug family #11: duplicate imports (e.g., _hexToRgba imported twice).
"""
import ast

import pytest

from backend.schemas import (
    PushSubscribeBody, PushUnsubscribeBody, PushUpdatePrefsBody,
    PushTestBody, ClientErrorBody, PushKeys,
)
from backend.state import BACKEND_DIR, FRONTEND_DIR


def test_push_subscribe_body_fields():
    """PushSubscribeBody has the fields the handler reads."""
    model = PushSubscribeBody.model_fields
    assert "endpoint" in model
    assert "keys" in model
    assert "client_id" in model
    assert "prefs" in model
    assert model["endpoint"].is_required()
    assert model["keys"].is_required()
    assert model["client_id"].is_required()
    assert not model["prefs"].is_required()


def test_push_keys_fields():
    """PushKeys has p256dh and auth, both required."""
    model = PushKeys.model_fields
    assert "p256dh" in model
    assert "auth" in model
    assert model["p256dh"].is_required()
    assert model["auth"].is_required()


def test_push_unsubscribe_body_fields():
    """PushUnsubscribeBody has optional endpoint and client_id."""
    model = PushUnsubscribeBody.model_fields
    assert "endpoint" in model
    assert "client_id" in model
    assert not model["endpoint"].is_required()
    assert not model["client_id"].is_required()


def test_push_update_prefs_body_fields():
    """PushUpdatePrefsBody has required prefs, optional client_id and endpoint."""
    model = PushUpdatePrefsBody.model_fields
    assert "prefs" in model
    assert "client_id" in model
    assert "endpoint" in model
    assert model["prefs"].is_required()
    assert not model["client_id"].is_required()


def test_push_test_body_fields():
    """PushTestBody has required endpoint."""
    model = PushTestBody.model_fields
    assert "endpoint" in model
    assert model["endpoint"].is_required()


def test_client_error_body_fields():
    """ClientErrorBody has required message, optional everything else."""
    model = ClientErrorBody.model_fields
    assert "message" in model
    assert "source" in model
    assert "line" in model
    assert "col" in model
    assert "stack" in model
    assert "type" in model
    assert "url" in model
    assert "ua" in model
    assert model["message"].is_required()
    assert not model["source"].is_required()


def test_no_openapi_extra_in_main():
    """main.py should not contain openapi_extra (replaced by Pydantic models)."""
    src = (BACKEND_DIR / "main.py").read_text()
    assert "openapi_extra" not in src, "main.py should not use openapi_extra (Pydantic models auto-generate schema)"


def test_push_routes_uses_models_not_parse_json_body():
    """push_routes.py should not call parse_json_body (Pydantic handles parsing)."""
    src = (BACKEND_DIR / "push_routes.py").read_text()
    assert "parse_json_body" not in src, "push_routes.py should not use parse_json_body (Pydantic handles body parsing)"


def test_no_duplicate_imports_in_chart_helpers():
    """chart-helpers.js should not import the same symbol twice."""
    frontend = FRONTEND_DIR / "js"
    src = (frontend / "chart-helpers.js").read_text()
    import_lines = [l.strip() for l in src.splitlines() if l.strip().startswith("import")]
    imported_names = []
    for line in import_lines:
        if "{" in line and "}" in line:
            block = line[line.index("{")+1:line.index("}")]
            for name in block.split(","):
                name = name.strip().split(" as ")[0].strip()
                if name:
                    imported_names.append(name)
    dups = [n for n in imported_names if imported_names.count(n) > 1]
    assert not dups, f"Duplicate imports in chart-helpers.js: {set(dups)}"
