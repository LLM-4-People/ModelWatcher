"""Test: docs/API.md documents exactly the app's REST endpoints, and the stated counts match.

Catches finding F37: adding /health/live left API.md and README.md saying "15 REST
endpoints" while the app served 16.
"""
import json
import re

import pytest

from backend.state import BASE_DIR
from scripts.tests.app_child import result

_DOCS_WITH_COUNTS = ["README.md", "docs/API.md"]
_COUNT_RE = re.compile(r"(\d+) REST endpoints(?: across (\d+) tags)?")


@pytest.fixture(scope="module")
def app_routes(run_python, example_config_env) -> dict:
    """The documented surface of the real app: its schema routes and tags (loaded in a clean process)."""
    code = (
        "from fastapi.routing import APIRoute\n"
        "from backend.main import app\n"
        "from scripts.tests.app_child import emit\n"
        "routes = sorted(f'{m} {r.path}' for r in app.routes if isinstance(r, APIRoute) and r.include_in_schema\n"
        "                for m in r.methods)\n"
        "emit({'routes': routes, 'tags': len(app.openapi_tags)})\n"
    )
    return result(run_python("-c", code, env=example_config_env))


def test_api_md_documents_every_route(app_routes):
    documented = sorted(re.findall(r"^### ((?:GET|POST|PUT|DELETE|PATCH) \S+)$", (BASE_DIR / "docs" / "API.md").read_text(), re.M))
    assert documented == app_routes["routes"]


@pytest.mark.parametrize("doc", _DOCS_WITH_COUNTS)
def test_stated_counts_match_the_app(app_routes, doc):
    counts = _COUNT_RE.findall((BASE_DIR / doc).read_text())
    assert counts, f"{doc} no longer states the endpoint count; drop it from _DOCS_WITH_COUNTS"
    for endpoints, tags in counts:
        assert int(endpoints) == len(app_routes["routes"])
        assert not tags or int(tags) == app_routes["tags"]


# ── F91: the documented response shapes are the real ones ───────────────────

API_MD = (BASE_DIR / "docs" / "API.md").read_text()


def _doc_json(marker: str) -> dict:
    """The first ```json block after marker in API.md, its `...` placeholders removed."""
    block = re.search(r"```json\n(.*?)\n```", API_MD.split(marker, 1)[1], re.S).group(1)
    block = re.sub(r",\s*\.\.\.(?=\s*[}\]])", "", block)
    block = re.sub(r"(?<=[\[{])\s*\.\.\.\s*(?=[}\]])", "", block)
    return json.loads(block)


@pytest.fixture(scope="module")
def served(run_python, example_config_env) -> dict:
    """/api/config and the WebSocket hello of the real app with the example config."""
    code = (
        "import backend.state as st\n"
        "from starlette.testclient import TestClient\n"
        "from backend.main import app\n"
        "from scripts.tests.app_child import emit, receive_until\n"
        "with TestClient(app, base_url='http://localhost') as client:\n"
        "    out = {'config': client.get('/api/config').json()}\n"
        "    with client.websocket_connect('ws://localhost' + st.WS_PATH, headers={'origin': 'http://localhost'}) as ws:\n"
        "        out['hello'] = receive_until(ws, lambda f: len(f) >= 1, 5)[0]\n"
        "emit(out)\n"
    )
    return result(run_python("-c", code, env={**example_config_env, "MW_DISABLE_TESTS": "1"}))


def test_config_example_has_the_real_fields(served):
    doc = _doc_json("### GET /api/config")
    real = served["config"]
    assert set(doc) == set(real), f"missing {set(real) - set(doc)}, stale {set(doc) - set(real)}"
    assert set(doc["scheduler"]) == set(real["scheduler"])
    assert set(doc["ui"]) <= set(real["ui"])
    assert set(doc["status_labels"]) == set(real["status_labels"])


def test_trend_example_has_the_real_shape(monkeypatch):
    """The trends example was {"tps": "up"}; a trend is an object with a signed delta."""
    from backend import stats
    import backend.state as st
    monkeypatch.setattr(st.c, "trend_deadbands", {"tps": 5.0}, raising=False)
    shape = set(stats._trend_entry("tps", 80.0, 90.0, 9))
    entry = next(iter(_doc_json("**Example - collection mode**").values()))
    trends = entry["trends"]
    assert isinstance(trends.pop("since_ts"), float)
    assert trends and all(set(t) == shape for t in trends.values()), trends
    assert all(t["direction"] in ("improving", "degrading", "stable") for t in trends.values())
    assert set(entry["scores"]) == {"consistency", "speed", "reliability"}
    assert all(1 < v <= 100 for v in entry["scores"].values()), "scores are 0-100"


def test_hello_documentation_names_every_field(served):
    hello_line = next(line for line in API_MD.splitlines() if line.startswith("- **`hello`**"))
    for key in served["hello"]:
        assert f'"{key}"' in hello_line, f"hello field {key} is not documented"
    assert "__MW_CONN__" not in API_MD
