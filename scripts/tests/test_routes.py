"""Test: route handlers answer correctly and log once per problem, not once per request.

Catches finding F18: /health logged a warning on every failing request, so the Docker
HEALTHCHECK on a degraded or MW_DISABLE_TESTS instance added one every 30s. F66:
the client error reporter read a bare `c` that routes.py never imported, so every
report from the browser failed with a 500 since v1.0.0. And F36: the page preloaded a
hand-kept module list that missed conn.js, filter.js and the chart helpers.
"""
import logging

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import backend.routes as routes
import backend.state as st


@pytest.fixture
def fresh_conditions(monkeypatch):
    monkeypatch.setattr(st, "_failing_conditions", set())


def test_condition_changed_reports_transitions_only(fresh_conditions):
    seen = [st.condition_changed("k", failing) for failing in (False, True, True, False, False, True)]
    assert seen == [False, True, False, True, False, True]
    assert st.condition_changed("other", True), "keys are independent"


def _set_readiness(monkeypatch, healthy: bool):
    monkeypatch.setattr(st, "scheduler_running", healthy)
    monkeypatch.setattr(st, "_healthy_model_count", 1 if healthy else 0)
    monkeypatch.setattr(st, "model_registry", [{"id": "P::m"}])


def test_readiness_logs_on_change_only(fresh_conditions, monkeypatch, caplog):
    statuses = []
    with caplog.at_level(logging.INFO, logger=st.log.name):
        for healthy in (False, False, False, True, True, False):
            _set_readiness(monkeypatch, healthy)
            statuses.append(routes.health_check().status_code)
    assert statuses == [503, 503, 503, 200, 200, 503]
    records = [(r.levelno, r.getMessage().split(":")[0]) for r in caplog.records]
    assert records == [
        (logging.WARNING, "Health check failing"),
        (logging.INFO, "Health check passing again"),
        (logging.WARNING, "Health check failing"),
    ]


def test_healthy_start_logs_nothing(fresh_conditions, monkeypatch, caplog):
    _set_readiness(monkeypatch, True)
    with caplog.at_level(logging.INFO, logger=st.log.name):
        assert routes.health_check().status_code == 200
    assert not caplog.records


def test_client_error_report_is_logged_and_rate_limited(monkeypatch, caplog):
    monkeypatch.setattr(st.c, "notif_rate_limit_client_error", 2, raising=False)
    monkeypatch.setattr(routes, "_client_error_times", {})
    app = FastAPI()
    app.post("/api/client-error")(routes.handle_client_error)
    body = {"message": "boom", "source": "app.js", "line": 3, "col": 7, "type": "error"}
    with caplog.at_level(logging.ERROR, logger=st.log.name), TestClient(app) as client:
        codes = [client.post("/api/client-error", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert [r.getMessage() for r in caplog.records if "[CLIENT]" in r.getMessage()][0].startswith(
        "[CLIENT] error boom at app.js:3:7")


def _static_imports(rel: str) -> set[str]:
    path = st.FRONTEND_DIR / rel
    return {(path.parent / m.group(3)).resolve().relative_to(st.FRONTEND_DIR).as_posix()
            for m in routes._JS_FROM_RE.finditer(path.read_text())}


def test_preload_order_is_the_static_import_closure_dependencies_first():
    order = routes.module_preload_order()
    assert order[-1] == "js/app.js" and len(order) == len(set(order))
    for n, rel in enumerate(order):
        assert _static_imports(rel) <= set(order[:n]), f"{rel} is preloaded before its imports"
    imported = set().union(*(_static_imports(rel) for rel in order))
    assert set(order) - imported == {"js/app.js"}, "only modules the entry loads statically are preloaded"
    assert {"js/conn.js", "js/filter.js"} <= set(order)
    assert "js/modal.js" not in order, "dynamic import() targets load on demand"
