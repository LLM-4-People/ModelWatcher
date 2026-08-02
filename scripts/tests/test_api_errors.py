"""Test: API error responses are uniform across all routes.

Catches bug family #3: 422 returned {"detail":[...]} while handlers
returned {"error":"msg"} - external tools couldn't parse errors uniformly.
"""
import json
import urllib.request
import urllib.error

import pytest

BASE = "https://stats.ai4fun.dev"


def _get_error(path):
    """Fetch a path that should error, return (status_code, body_dict)."""
    try:
        r = urllib.request.urlopen(f"{BASE}{path}")
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_400_handler_error_format():
    """Handler-level 400 returns {"error": "msg"}."""
    code, body = _get_error("/api/metrics?type=invalid")
    assert code == 400
    assert "error" in body
    assert "detail" not in body


def test_422_validation_error_format():
    """FastAPI 422 validation returns {"error": "msg"} (not {"detail": [...]})."""
    code, body = _get_error(f"/api/audit?model={'x'*300}")
    assert code == 422
    assert "error" in body
    assert "detail" not in body


def test_404_error_format():
    """404 returns {"error": "msg"} (not {"detail": "Not Found"})."""
    code, body = _get_error("/api/nonexistent")
    assert code == 404
    assert "error" in body
    assert "detail" not in body


def test_400_empty_model():
    """Empty model param returns {"error": "msg"}."""
    code, body = _get_error("/api/metrics?model=&type=card")
    assert code == 400
    assert "error" in body


def test_400_bad_since():
    """Bad 'since' param on /api/notifications returns {"error": "msg"}."""
    code, body = _get_error("/api/notifications?since=not-a-date")
    assert code == 400
    assert "error" in body


def test_422_bad_float():
    """Bad float param on /api/metrics returns 422 with {"error": "msg"}."""
    code, body = _get_error("/api/metrics?model=x&type=card&since=abc")
    assert code == 422
    assert "error" in body
    assert "detail" not in body


def test_no_bare_dict_returns_in_push_handlers():
    """push_routes.py should not return bare dicts (should use orjson_response)."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "backend" / "push_routes.py"
    text = src.read_text()
    assert 'return {"ok": True}' not in text, "push_routes.py should use orjson_response, not bare dict returns"
    assert 'return {"ok": True,' not in text, "push_routes.py should use orjson_response, not bare dict returns"


def test_error_response_helper_exists():
    """routes.py defines error_response() helper."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "backend" / "routes.py"
    text = src.read_text()
    assert "def error_response(" in text


def test_no_raw_jsonresponse_error_in_handlers():
    """No JSONResponse({"error":...}) outside error_response definition."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "backend" / "push_routes.py"
    for i, line in enumerate(src.read_text().splitlines(), 1):
        if 'JSONResponse({"error"' in line:
            pytest.fail(f"push_routes.py:{i} uses raw JSONResponse error instead of error_response()")
