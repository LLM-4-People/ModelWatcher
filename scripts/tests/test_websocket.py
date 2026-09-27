"""Test: the dashboard's own page can hold a live WebSocket, and the socket reports what happened.

Catches finding F5: the origin check only consulted websocket.allowed_origins, which
ships as the placeholder site_url, so the page served by the very same host was
closed right after it opened (code 4403) and the header dot stayed red. Also covers
what the client needs to read the socket correctly: a hello frame as proof of
acceptance, distinct close codes for "never" (policy) and "later" (connection limit),
and a heartbeat that keeps a quiet server's socket observably alive even with
MW_DISABLE_TESTS set.
"""
import asyncio
import copy
import json
from contextlib import asynccontextmanager

import pytest
import yaml
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected

import backend.state as st
import backend.websocket as ws_module
from backend.routes import module_preload_order
from backend.state import CONFIG_DIR
from backend.websocket import CLOSE_CODES, connection_config, is_allowed_origin, websocket_endpoint, ws_mgr
from scripts.tests.app_child import result

APP_EXAMPLE = yaml.safe_load((CONFIG_DIR / "app.yaml.example").read_text())
WS_EXAMPLE = APP_EXAMPLE["websocket"]
SITE_URL = APP_EXAMPLE["app"]["site_url"]
# TestClient sends Host "testserver"; a page it served carries this Origin
SAME_ORIGIN = "http://testserver"
# The real app only answers Host names that name it (F48); localhost always does
LOCAL_ORIGIN = "http://localhost"
OTHER_ORIGIN = "https://elsewhere.example.org"
# Real-app child: heartbeat period, and the deadline for a hello plus two heartbeats
HEARTBEAT_S = 0.2
FRAMES_DEADLINE_S = 5


@pytest.fixture
def ws_config(monkeypatch):
    """The example's websocket settings on the live config namespace, as reload_config sets them."""
    values = {
        "allowed_ws_origins": set(WS_EXAMPLE["allowed_origins"]),
        "max_connections": APP_EXAMPLE["server"]["max_connections"],
        "ws_heartbeat_interval": WS_EXAMPLE["heartbeat_interval"],
        "ws_stale_after": WS_EXAMPLE["stale_after"],
        "ws_reconnect": dict(WS_EXAMPLE["reconnect"]),
        "ws_unreachable": dict(WS_EXAMPLE["unreachable"]),
        "ws_max_message_bytes": WS_EXAMPLE["max_message_bytes"],
        "ws_sync_prefs_per_minute": WS_EXAMPLE["sync_prefs_per_minute"],
    }
    for name, value in values.items():
        monkeypatch.setattr(ws_module.c, name, value, raising=False)
    monkeypatch.setattr(ws_mgr, "connections", [])
    monkeypatch.setattr(ws_mgr, "_prefs", {})
    return ws_module.c


def _app(heartbeat: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        if heartbeat:
            ws_mgr.start_heartbeat()
        yield
        for task in list(st._background_tasks):
            task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.websocket(st.WS_PATH)(websocket_endpoint)
    return app


def _close_code(client: TestClient, origin: str | None) -> int | None:
    """Connect and read the first frame; the close code if the server closed first, else None."""
    headers = {"origin": origin} if origin else {}
    with client.websocket_connect(st.WS_PATH, headers=headers) as ws:
        try:
            assert ws.receive_json()["type"] == "hello"
        except WebSocketDisconnect as e:
            return e.code
    return None


@pytest.mark.parametrize("origin, host, allowlist, allowed", [
    ("http://127.0.0.1:8080", "127.0.0.1:8080", {SITE_URL}, True),
    ("http://LocalHost:8080", "localhost:8080", {SITE_URL}, True),
    ("https://example.com", "example.com", {SITE_URL}, True),
    ("http://127.0.0.1:8080", "127.0.0.1:9090", {SITE_URL}, False),
    (OTHER_ORIGIN, "127.0.0.1:8080", {SITE_URL}, False),
    ("", "127.0.0.1:8080", {SITE_URL}, False),
    (SITE_URL, "127.0.0.1:8080", {SITE_URL}, True),
    (OTHER_ORIGIN, "127.0.0.1:8080", set(), True),
    ("http://127.0.0.1:8080/path", "127.0.0.1:8080", {SITE_URL}, False),
    ("ws://127.0.0.1:8080", "127.0.0.1:8080", {SITE_URL}, False),
    # Malformed origins are untrusted input: rejected, never an exception
    ("http://[", "127.0.0.1:8080", {SITE_URL}, False),
    ("http://a]b", "127.0.0.1:8080", {SITE_URL}, False),
    ("null", "127.0.0.1:8080", {SITE_URL}, False),
])
def test_origin_rule(monkeypatch, origin, host, allowlist, allowed):
    monkeypatch.setattr(ws_module.c, "allowed_ws_origins", allowlist, raising=False)
    assert is_allowed_origin(origin, host) is allowed


def test_same_origin_page_is_accepted_with_placeholder_allowlist(ws_config):
    """The F5 reproduction: allowlist = [site_url] placeholder, page served by this host."""
    assert ws_config.allowed_ws_origins == {SITE_URL}
    with TestClient(_app()) as client:
        with client.websocket_connect(st.WS_PATH, headers={"origin": SAME_ORIGIN}) as ws:
            hello = ws.receive_json()
    assert hello == {"type": "hello", "config": json.loads(json.dumps(connection_config()))}


@pytest.mark.parametrize("origin, allowlist, expected", [
    (OTHER_ORIGIN, {SITE_URL}, CLOSE_CODES["policy"]),
    (None, {SITE_URL}, CLOSE_CODES["policy"]),
    (SITE_URL, {SITE_URL}, None),
    (OTHER_ORIGIN, set(), None),
    ("http://[", {SITE_URL}, CLOSE_CODES["policy"]),
])
def test_cross_origin_uses_allowlist(ws_config, monkeypatch, origin, allowlist, expected):
    monkeypatch.setattr(ws_config, "allowed_ws_origins", allowlist)
    with TestClient(_app()) as client:
        assert _close_code(client, origin) == expected


def test_connection_limit_is_a_retry_later_close(ws_config, monkeypatch):
    monkeypatch.setattr(ws_config, "max_connections", 0)
    with TestClient(_app()) as client:
        assert _close_code(client, SAME_ORIGIN) == CLOSE_CODES["try_again"]


def test_permanent_and_transient_rejections_differ():
    assert CLOSE_CODES["policy"] != CLOSE_CODES["try_again"]
    assert len(set(CLOSE_CODES.values())) == len(CLOSE_CODES)


def test_oversized_message_closes_with_too_big(ws_config, monkeypatch):
    monkeypatch.setattr(ws_config, "ws_max_message_bytes", 8)
    with TestClient(_app()) as client:
        with client.websocket_connect(st.WS_PATH, headers={"origin": SAME_ORIGIN}) as ws:
            ws.receive_json()
            ws.send_text("x" * 9)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_CODES["too_big"]


def test_sync_prefs_rate_limit_comes_from_config(ws_config, monkeypatch):
    monkeypatch.setattr(ws_config, "ws_sync_prefs_per_minute", 1)
    stored = []
    monkeypatch.setattr(ws_mgr, "set_prefs", lambda _ws, prefs: stored.append(prefs))
    with TestClient(_app()) as client:
        with client.websocket_connect(st.WS_PATH, headers={"origin": SAME_ORIGIN}) as ws:
            ws.receive_json()
            for n in range(3):
                ws.send_json({"type": "sync_prefs", "prefs": {"enabled": bool(n)}})
            ws.send_text("x" * (ws_config.ws_max_message_bytes + 1))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert stored == [{"enabled": False}]


def test_heartbeat_reaches_a_quiet_socket(ws_config, monkeypatch):
    monkeypatch.setattr(ws_config, "ws_heartbeat_interval", 0.05)
    with TestClient(_app(heartbeat=True)) as client:
        with client.websocket_connect(st.WS_PATH, headers={"origin": SAME_ORIGIN}) as ws:
            frames = [ws.receive_json()["type"] for _ in range(3)]
    assert frames == ["hello", "heartbeat", "heartbeat"]


def test_heartbeat_survives_a_failed_broadcast(ws_config, monkeypatch):
    monkeypatch.setattr(ws_config, "ws_heartbeat_interval", 0)
    calls = []

    async def flaky(msg, filter_fn=None):
        calls.append(msg)
        if len(calls) == 1:
            raise RuntimeError("send failed")
        raise asyncio.CancelledError

    monkeypatch.setattr(ws_mgr, "broadcast", flaky)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ws_mgr._heartbeat_loop())
    assert len(calls) == 2


class _ClosingSocket:
    """Stands in for a WebSocket whose close() succeeds or raises the given error."""

    def __init__(self, error: Exception | None = None):
        self.error, self.closed_with = error, None

    async def close(self, code: int, reason: str = ""):
        self.closed_with = code
        if self.error:
            raise self.error


def test_close_all_tells_a_gone_client_from_a_failure(ws_config, monkeypatch, caplog):
    """F47: a close failure was logged at debug level whatever it was, hiding real errors."""
    sockets = [_ClosingSocket(), _ClosingSocket(WebSocketDisconnected("already closed")),
               _ClosingSocket(WebSocketDisconnect(1006)), _ClosingSocket(ValueError("bug"))]
    monkeypatch.setattr(ws_mgr, "connections", list(sockets))
    with caplog.at_level("DEBUG", logger=st.log.name):
        asyncio.run(ws_mgr.close_all())
    assert [s.closed_with for s in sockets] == [CLOSE_CODES["restart"]] * 4, "one failure must not stop the others"
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and isinstance(errors[0].exc_info[1], ValueError)
    assert any(r.levelname == "INFO" and "2/4 clients had already disconnected" in r.getMessage() for r in caplog.records)
    assert ws_mgr.connections == []


def test_real_app_with_tests_disabled(run_python, example_config_env, tmp_path):
    """Boot backend.main with the shipped example configs and MW_DISABLE_TESTS=1, as DEVELOPMENT.md does.

    Covers the whole chain the browser sees: the bootstrap config, the hello frame,
    heartbeats on a server that runs no tests, liveness vs readiness, and the Host
    check (F48): the page is served as http://localhost, a rebinding name gets 400.
    Frames are read with a deadline (F42), so missing heartbeats fail the assertion
    below within a few heartbeat periods instead of hanging until the subprocess timeout.
    """
    cfg = copy.deepcopy(APP_EXAMPLE)
    cfg["websocket"]["heartbeat_interval"] = HEARTBEAT_S
    cfg["websocket"]["stale_after"] = 1
    cfg["app"]["log_level"] = "info"
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(yaml.safe_dump(cfg))
    code = (
        "import json, re\n"
        "from starlette.testclient import TestClient\n"
        "import backend.state as st\n"
        "from backend.main import app, _OUTBOUND_TASKS\n"
        "from scripts.tests.app_child import emit, receive_until\n"
        f"with TestClient(app, base_url={LOCAL_ORIGIN!r}) as client:\n"
        "    out = {'live': client.get(st.LIVENESS_PATH).status_code, 'ready': client.get('/health').status_code,\n"
        "           'foreign_host': client.get(st.LIVENESS_PATH, headers={'host': 'rebind.example.org'}).status_code,\n"
        "           'test_tasks': list(_OUTBOUND_TASKS)}\n"
        "    page = client.get('/').text\n"
        "    out['preloads'] = re.findall(r'rel=\"modulepreload\" href=\"[^\"]*?/(js/[^\"?]+)', page)\n"
        "    out['boot'] = json.loads(page.split('window.__MW_CONN__=', 1)[1].split('</script>', 1)[0])\n"
        f"    with client.websocket_connect('ws://localhost' + st.WS_PATH, headers={{'origin': {LOCAL_ORIGIN!r}}}) as ws:\n"
        f"        out['frames'] = receive_until(ws, lambda f: len(f) >= 3, {FRAMES_DEADLINE_S})\n"
        "emit(out)\n"
    )
    proc = run_python("-c", code, env={**example_config_env, "MW_APP_YAML": str(app_yaml), "MW_DISABLE_TESTS": "1"})
    out = result(proc)
    assert out["live"] == 200
    assert out["ready"] == 503, "readiness stays 503 without a scheduler; liveness must not follow it"
    assert out["foreign_host"] == 400
    assert out["preloads"] == module_preload_order(), "the page preloads the derived module list (F36)"
    assert [f["type"] for f in out["frames"]] == ["hello", "heartbeat", "heartbeat"], \
        f"expected a hello and two heartbeats within {FRAMES_DEADLINE_S}s"
    hello = out["frames"][0]
    assert hello["config"] == out["boot"]
    assert out["boot"]["stale_after"] == 1
    assert out["boot"]["reconnect"] == cfg["websocket"]["reconnect"]
    assert out["boot"]["unreachable"] == cfg["websocket"]["unreachable"]
    assert out["boot"]["close_codes"] == CLOSE_CODES
    assert "scheduler" in out["test_tasks"]
    assert f"MW_DISABLE_TESTS set - not starting: {', '.join(out['test_tasks'])}" in proc.stderr

