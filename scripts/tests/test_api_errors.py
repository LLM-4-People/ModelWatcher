"""Test: API error responses are uniform ({"error": "message"} and nothing else) across all routes.

Catches bug family #3: 422 returned {"detail":[...]} while handlers returned {"error":"msg"},
so external tools could not parse errors uniformly. And finding F8: this file used to call the
production host, so it failed offline and checked whatever that deployment ran instead of this
checkout. It now runs the checked-out app on the example configs in a child process, one case
per source of error responses: handlers, query and body validation, routing, and middleware.
"""
import json

import pytest

from backend.state import BACKEND_DIR, MAX_REQUEST_BODY_BYTES
from scripts.tests.app_child import result

# (method, path, request kwargs, expected status), keyed by the layer that answers. `body_bytes`
# stands for a body of that size, built in the child (too big for a command-line argument).
ERROR_CASES = {
    "handler: type without model": ("GET", "/api/metrics?type=invalid", {}, 400),
    "handler: empty model": ("GET", "/api/metrics?model=&type=card", {}, 400),
    "handler: bad since": ("GET", "/api/notifications?since=not-a-date", {}, 400),
    "query validation: too long": ("GET", f"/api/audit?model={'x' * 300}", {}, 422),
    "query validation: not a float": ("GET", "/api/metrics?model=x&type=card&since=abc", {}, 422),
    "body validation": ("POST", "/api/client-error", {"json": {"unexpected": 1}}, 422),
    "router: unknown path": ("GET", "/api/nonexistent", {}, 404),
    "router: wrong method": ("DELETE", "/api/metrics", {}, 405),
    "middleware: foreign Host": ("GET", "/api/config", {"headers": {"host": "rebind.example.org"}}, 400),
    "middleware: body too large": ("POST", "/api/client-error", {"body_bytes": MAX_REQUEST_BODY_BYTES + 1}, 413),
}

_CHILD = """
import json, sys
from starlette.testclient import TestClient
from backend.main import app
from scripts.tests.app_child import emit

cases = json.loads(sys.argv[1])
with TestClient(app, base_url='http://localhost') as client:
    out = {}
    for name, (method, path, kwargs) in cases.items():
        if 'body_bytes' in kwargs:
            kwargs['content'] = b'x' * kwargs.pop('body_bytes')
        r = client.request(method, path, **kwargs)
        out[name] = {'status': r.status_code, 'type': r.headers.get('content-type'), 'body': r.text}
emit(out)
"""


@pytest.fixture(scope="module")
def responses(run_python, example_config_env) -> dict:
    """Every case answered by the real app (backend.main) on the example configs, tests disabled."""
    cases = {name: case[:3] for name, case in ERROR_CASES.items()}
    proc = run_python("-c", _CHILD, json.dumps(cases), env={**example_config_env, "MW_DISABLE_TESTS": "1"})
    return result(proc)


@pytest.mark.parametrize("name", ERROR_CASES)
def test_error_response_format(responses, name):
    expected_status = ERROR_CASES[name][3]
    got = responses[name]
    assert got["status"] == expected_status, got
    assert got["type"] == "application/json", got
    body = json.loads(got["body"])
    assert list(body) == ["error"], f"error bodies carry only 'error': {body}"
    assert isinstance(body["error"], str) and body["error"], body


def test_no_bare_dict_returns_in_push_handlers():
    """push_routes.py should not return bare dicts (should use orjson_response)."""
    src = BACKEND_DIR / "push_routes.py"
    text = src.read_text()
    assert 'return {"ok": True}' not in text, "push_routes.py should use orjson_response, not bare dict returns"
    assert 'return {"ok": True,' not in text, "push_routes.py should use orjson_response, not bare dict returns"


def test_error_response_helper_exists():
    """routes.py defines error_response() helper."""
    src = BACKEND_DIR / "routes.py"
    text = src.read_text()
    assert "def error_response(" in text


def test_no_raw_jsonresponse_error_in_handlers():
    """No JSONResponse({"error":...}) outside error_response definition."""
    src = BACKEND_DIR / "push_routes.py"
    for i, line in enumerate(src.read_text().splitlines(), 1):
        if 'JSONResponse({"error"' in line:
            pytest.fail(f"push_routes.py:{i} uses raw JSONResponse error instead of error_response()")
