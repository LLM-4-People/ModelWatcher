"""Test: requests and WebSockets are only served under a Host that names this server.

Catches finding F48: nothing validated the Host header, and the F5 same-origin rule
trusts it, so a DNS-rebinding page (attacker's name, resolving to this server) counted
as same-origin and got the WebSocket, as it already got the HTTP API. The check sits in
HostCheckMiddleware, the one choke point for both.
"""
import json

import pytest
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.middleware as middleware
import backend.state as st

SITE_HOST = "dash.example.com"


@pytest.fixture
def hosts(monkeypatch):
    """The example's defaults: no extra names, site_url on SITE_HOST."""
    monkeypatch.setattr(middleware.c, "site_host", SITE_HOST, raising=False)
    monkeypatch.setattr(middleware.c, "allowed_hosts", (), raising=False)
    monkeypatch.setattr(st, "_failing_conditions", set())
    return middleware.c


@pytest.mark.parametrize("host, allowed", [
    # Same-origin dev and LAN access by address keeps working with no configuration
    ("127.0.0.1:8080", True), ("localhost:8080", True), ("LOCALHOST", True), ("[::1]:8080", True),
    ("192.168.1.20:8080", True), ("10.0.0.5", True),
    # The public name from app.site_url, behind a proxy that forwards Host with or without a port
    (SITE_HOST, True), (f"{SITE_HOST}:443", True),
    # Rebinding names and malformed headers are rejected
    ("rebind.attacker.example", False), (f"{SITE_HOST}.attacker.example", False), (f"x{SITE_HOST}", False),
    ("", False), ("[::1", False), (":8080", False),
])
def test_host_rule_defaults(hosts, host, allowed):
    assert middleware.host_allowed(host) is allowed


@pytest.mark.parametrize("patterns, host, allowed", [
    (("nas.local",), "nas.local:8080", True),
    (("nas.local",), "other.local", False),
    (("*.example.org",), "a.example.org", True),
    (("*.example.org",), "a.b.example.org", True),
    (("*.example.org",), "example.org", False),
    (("*.example.org",), "badexample.org", False),
    (("*",), "anything.at.all", True),
])
def test_host_rule_patterns(hosts, monkeypatch, patterns, host, allowed):
    monkeypatch.setattr(hosts, "allowed_hosts", patterns)
    assert middleware.host_allowed(host) is allowed


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"type": "hello"})
        await ws.close()

    app.add_middleware(middleware.HostCheckMiddleware)
    return app


def test_http_with_foreign_host_gets_400(hosts):
    with TestClient(_app(), base_url="http://rebind.attacker.example") as client:
        resp = client.get("/ping")
    assert resp.status_code == 400
    assert resp.json() == {"error": "Invalid host header"}


def test_http_with_own_host_is_served(hosts):
    for base in ("http://127.0.0.1:8080", "http://localhost", f"https://{SITE_HOST}"):
        with TestClient(_app(), base_url=base) as client:
            assert client.get("/ping").json() == {"ok": True}, base


# TestClient.websocket_connect ignores base_url, so the tests pass absolute ws:// URLs
def test_websocket_with_foreign_host_is_closed_before_accept(hosts):
    with TestClient(_app()) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("ws://rebind.attacker.example/ws") as ws:
                ws.receive_json()
    assert closed.value.code == 1008


def test_websocket_with_own_host_is_accepted(hosts):
    with TestClient(_app()) as client:
        with client.websocket_connect("ws://127.0.0.1:8080/ws") as ws:
            assert ws.receive_json() == {"type": "hello"}


def test_rejections_warn_once(hosts, caplog):
    with caplog.at_level("DEBUG", logger=st.log.name), TestClient(_app(), base_url="http://a.attacker.example") as client:
        for _ in range(3):
            client.get("/ping")
    levels = [r.levelname for r in caplog.records if "Rejected Host" in r.getMessage()]
    assert levels == ["WARNING", "DEBUG", "DEBUG"]
    assert "server.allowed_hosts" in caplog.records[0].getMessage()


def test_example_config_accepts_its_site_url_host(run_python):
    """app.yaml.example loads into the names the middleware reads."""
    code = (
        "import json\n"
        "from backend.config import reload_config\n"
        "import backend.state as st\n"
        "reload_config()\n"
        "print(json.dumps([st.c.site_host, list(st.c.allowed_hosts)]))\n"
    )
    out = run_python("-c", code, env={
        "MW_APP_YAML": "app.yaml.example", "MW_MODELS_YAML": "models.yaml.example",
        "MW_AUDITS_YAML": "audits.yaml.example",
    }).stdout.strip().splitlines()[-1]
    assert json.loads(out) == ["your-domain.example.com", []]
