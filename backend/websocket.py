"""WebSocket connection manager, origin validation, heartbeat, and endpoint handler.

The WSManager tracks live connections and per-connection notification
prefs, enabling server-side filtering of broadcasts. Every accepted socket
gets a hello frame first; a heartbeat keeps quiet sockets observably alive.
The endpoint handler is binary-safe, message-size-limited, and rate-limits
prefs sync.
"""

import asyncio

import orjson
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketDisconnected

import backend.state as st
from backend.state import c, log, sanitize_prefs

# Close codes the browser acts on (RFC 6455 plus the private 4000-4999 range).
# connection_config() hands them to frontend/js/ws.js, which never hardcodes them.
CLOSE_CODES = {
    "normal": 1000,
    "going_away": 1001,  # browser tab closed or navigated away
    "restart": 1012,     # server restarting: reconnect promptly
    "policy": 1008,      # origin not allowed: permanent for this page, retry at the slowest pace
    "try_again": 1013,   # connection limit reached: transient, normal backoff
    "too_big": 1009,     # message over websocket.max_message_bytes
    "internal": 1011,    # handler error
    "stale": 4000,       # sent by the client when nothing arrived within stale_after
}

_HEARTBEAT = {"type": "heartbeat"}
# What Starlette raises when a client left first: sending on a closed socket, or a dead transport (1006)
_CLIENT_GONE = (WebSocketDisconnected, WebSocketDisconnect)


def connection_config() -> dict:
    """Connection policy the browser follows: staleness, reconnect pacing, unreachable detection.

    One builder for both deliveries: the page bootstrap (needed before the first
    connect attempt) and every hello frame (so a config reload reaches open pages).
    """
    return {
        "ws_path": st.WS_PATH,
        "liveness_path": st.LIVENESS_PATH,
        "stale_after": c.ws_stale_after,
        "reconnect": c.ws_reconnect,
        "unreachable": c.ws_unreachable,
        "close_codes": CLOSE_CODES,
    }


class WSManager:
    def __init__(self):
        self.connections: list[WebSocket] = []
        self._prefs: dict[WebSocket, dict] = {}  # per-connection notification prefs (ephemeral)
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> bool:
        """Greet an accepted socket and start broadcasting to it; False when the connection limit is reached.

        The hello goes out before the socket joins `connections`, so it is always the
        first frame: the client treats it as the proof that the server accepted it.
        """
        async with self._lock:
            if len(self.connections) >= c.max_connections:
                return False
            await ws.send_text(orjson.dumps({"type": "hello", "config": connection_config(),
                                             "scheduler": st.scheduler_state()}).decode())
            self.connections.append(ws)
            return True

    def disconnect(self, ws: WebSocket):
        try:
            self.connections.remove(ws)
        except ValueError:
            log.debug("WS disconnect: connection not in list (already removed)")
        self._prefs.pop(ws, None)

    def set_prefs(self, ws: WebSocket, prefs: dict):
        """Store sanitized notification preferences for a WS connection."""
        self._prefs[ws] = sanitize_prefs(prefs)

    async def broadcast(self, msg: dict, filter_fn=None):
        """Broadcast a message to all connections, optionally filtered by prefs.

        When filter_fn is provided, connections with non-None prefs that fail
        the filter are skipped. Connections with None prefs (no sync yet) are
        always sent to. Dead connections discovered during send are removed.
        """
        data = orjson.dumps(msg).decode()
        if not self.connections:
            return
        dead = []
        tasks = []
        for ws in self.connections:
            prefs = self._prefs.get(ws)
            if filter_fn is not None and prefs is not None and not filter_fn(prefs):
                continue
            async def _send(w=ws):
                try:
                    await w.send_text(data)
                except _CLIENT_GONE as e:
                    log.debug("WS broadcast: dropping a closed socket (%s)", type(e).__name__)
                    dead.append(w)
                except Exception as e:
                    log.warning("WS broadcast send failed: %s", e)
                    dead.append(w)
            tasks.append(st.create_task(_send()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for ws in dead:
            self.disconnect(ws)

    def start_heartbeat(self):
        """Broadcast a heartbeat every websocket.heartbeat_interval seconds.

        Clients reconnect after websocket.stale_after seconds of silence, so a server
        with nothing to report (tests disabled, no models) must still speak. Started
        unconditionally at startup for that reason.
        """
        st.create_task(self._heartbeat_loop(), name="ws_heartbeat")

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(c.ws_heartbeat_interval)
            try:
                await self.broadcast(_HEARTBEAT)
            except Exception as e:
                st.log_error("WS heartbeat broadcast failed", e)

    async def shutdown_notify(self, reason: str = "restart"):
        """Best-effort broadcast of a server_shutdown message before closing."""
        try:
            await self.broadcast({"type": "server_shutdown", "reason": reason})
        except Exception as e:
            log.warning("Shutdown notify failed: %s", e)

    async def close_all(self):
        """Close every connection with the restart code so clients reconnect promptly."""
        conns = list(self.connections)
        self.connections.clear()
        self._prefs.clear()
        gone = 0
        for ws in conns:
            try:
                await ws.close(code=CLOSE_CODES["restart"], reason="server restarting")
            except _CLIENT_GONE as e:
                log.debug("WS close_all: client already gone (%s)", type(e).__name__)
                gone += 1
            except Exception as e:
                st.log_error("WS close_all: close failed", e)
        if gone:
            log.info("WS close_all: %d/%d clients had already disconnected", gone, len(conns))


ws_mgr = WSManager()


def is_allowed_origin(origin: str, host: str) -> bool:
    """Accept pages served by this host, then origins on the allowlist (empty list = every origin).

    A same-origin page cannot be cross-site WebSocket hijacking, which is what the
    check exists to stop, so it must not depend on allowed_origins listing every
    name the server is reached by (finding F5: localhost and LAN access were rejected).
    Browsers serialize Origin as scheme://host[:port], so same-origin is a plain string match
    against the Host header; parsing the untrusted header could raise on input like "http://[".
    """
    if host and origin.lower() in (f"http://{host.lower()}", f"https://{host.lower()}"):
        return True
    return not c.allowed_ws_origins or origin in c.allowed_ws_origins


async def websocket_endpoint(ws: WebSocket):
    _client_host = ws.client.host if ws.client else "unknown"
    origin = ws.headers.get("origin", "")
    # Accept before any rejection: a close code only reaches the browser on an open socket
    await ws.accept()
    if not is_allowed_origin(origin, ws.headers.get("host", "")):
        log.warning("WS connection rejected - forbidden origin: %s", origin or "none")
        await ws.close(code=CLOSE_CODES["policy"], reason="forbidden origin")
        return
    _sync_prefs_times: list[float] = []
    try:
        if not await ws_mgr.register(ws):
            log.warning("WS connection rejected - max connections reached (%s)", _client_host)
            await ws.close(code=CLOSE_CODES["try_again"], reason="max connections")
            return
        log.info("WS client connected (%s)", _client_host)
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg["type"] == "websocket.receive":
                if "bytes" in msg:
                    continue
                data = msg.get("text", "")
            else:
                continue
            # Oversized messages close the connection - deliberate security measure
            if len(data) > c.ws_max_message_bytes:
                log.warning("WS oversized message (%d bytes, %s)", len(data), _client_host)
                await ws.close(code=CLOSE_CODES["too_big"], reason="message too big")
                break
            if data.startswith("{"):
                try:
                    parsed = orjson.loads(data)
                    if not isinstance(parsed, dict):
                        continue
                    if parsed.get("type") == "sync_prefs":
                        if st.rate_limited(_sync_prefs_times, 60, c.ws_sync_prefs_per_minute):
                            log.warning("WS sync_prefs rate limited (%s)", _client_host)
                            continue
                        raw_prefs = parsed.get("prefs", {})
                        if isinstance(raw_prefs, dict):
                            ws_mgr.set_prefs(ws, raw_prefs)
                except (orjson.JSONDecodeError, ValueError):
                    log.warning("Invalid WS message from %s (len=%d)", _client_host, len(data))
    except WebSocketDisconnect as e:
        if e.code in (CLOSE_CODES["normal"], CLOSE_CODES["going_away"]):
            log.info("WS client disconnected (%s, code %d)", _client_host, e.code)
        elif e.code == CLOSE_CODES["restart"]:
            log.info("WS client disconnected - server shutting down (%s)", _client_host)
        else:
            log.warning("WS client disconnected (%s, code %d)", _client_host, e.code)
    except Exception as e:
        st.log_error("WS handler error", e)
        try:
            await ws.close(code=CLOSE_CODES["internal"], reason="internal error")
        except Exception as e2:
            st.log_error("WS close after handler error failed", e2)
    finally:
        ws_mgr.disconnect(ws)
