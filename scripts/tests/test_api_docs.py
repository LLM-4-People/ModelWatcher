"""Test: docs/API.md documents exactly the app's REST endpoints, and the stated counts match.

Catches finding F37: adding /health/live left API.md and README.md saying "15 REST
endpoints" while the app served 16.
"""
import json
import re

import pytest

from backend.state import BASE_DIR

_DOCS_WITH_COUNTS = ["README.md", "docs/API.md"]
_COUNT_RE = re.compile(r"(\d+) REST endpoints(?: across (\d+) tags)?")


@pytest.fixture(scope="module")
def app_routes(run_python) -> dict:
    """The documented surface of the real app: its schema routes and tags (loaded in a clean process)."""
    code = (
        "import json\n"
        "from fastapi.routing import APIRoute\n"
        "from backend.main import app\n"
        "routes = sorted(f'{m} {r.path}' for r in app.routes if isinstance(r, APIRoute) and r.include_in_schema\n"
        "                for m in r.methods)\n"
        "print('RESULT ' + json.dumps({'routes': routes, 'tags': len(app.openapi_tags)}))\n"
    )
    out = run_python("-c", code, env={
        "MW_APP_YAML": "app.yaml.example", "MW_MODELS_YAML": "models.yaml.example", "MW_AUDITS_YAML": "audits.yaml.example",
    }).stdout
    return json.loads(out.split("RESULT ", 1)[1])


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
