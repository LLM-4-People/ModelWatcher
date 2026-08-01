"""WebSocket connection manager, origin validation, and endpoint handler.

The WSManager tracks live connections and per-connection notification
prefs, enabling server-side filtering of broadcasts. The endpoint
handler is binary-safe, message-size-limited, and rate-limits prefs sync.
"""

import asyncio
import time

import orjson
from fastapi import WebSocket, WebSocketDisconnect

import backend.state as st
from backend.state import c, log, sanitize_prefs


class WSManager:
    def __init__(self):
        self.connections: list[WebSocket] = []
        self._prefs: dict[WebSocket, dict] = {}  # per-connection notification prefs (ephemeral)
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        async with self._lock:
            if len(self.connections) >= c.max_connections:
                return None
            await ws.accept()
            self.connections.append(ws)
            return ws

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
                except RuntimeError:
                    dead.append(w)
                except Exception as e:
                    log.warning("WS broadcast send failed: %s", e)
                    dead.append(w)
            tasks.append(st.create_task(_send()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for ws in dead:
            self.disconnect(ws)

    async def shutdown_notify(self, reason: str = "restart"):
        """Best-effort broadcast of a server_shutdown message before closing."""
        try:
            await self.broadcast({"type": "server_shutdown", "reason": reason})
        except Exception as e:
            log.warning("Shutdown notify failed: %s", e)

    async def close_all(self, code: int = 1001, reason: str = "server restarting"):
        """Gracefully close all WebSocket connections with a close frame."""
        conns = list(self.connections)
        self.connections.clear()
        self._prefs.clear()
        fail_count = 0
        for ws in conns:
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                fail_count += 1
        if fail_count:
            log.warning("WS close_all: %d/%d connections failed to close", fail_count, len(conns))


ws_mgr = WSManager()


def is_allowed_origin(origin: str) -> bool:
    """Return True if origin is allowed, or if no origin allowlist is configured."""
    if not c.allowed_ws_origins:
        return True
    return origin in c.allowed_ws_origins


# Oversized messages close the connection (code 1009) - deliberate security measure
_MAX_WS_MESSAGE_SIZE = 10 * 1024
_SYNC_PREFS_WINDOW = 60
_SYNC_PREFS_MAX = 30


async def websocket_endpoint(ws: WebSocket):
    _client_host = ws.client.host if ws.client else "unknown"
    origin = ws.headers.get("origin", "")
    if not is_allowed_origin(origin):
        st.log.warning("WS connection rejected - forbidden origin: %s", origin or "none")
        await ws.accept()
        await ws.close(code=4403, reason="forbidden origin")
        return
    conn = await ws_mgr.connect(ws)
    if conn is None:
        st.log.warning("WS connection rejected - max connections reached (%s)", _client_host)
        await ws.accept()
        await ws.close(code=4403, reason="max connections")
        return
    st.log.info("WS client connected (%s)", _client_host)
    _sync_prefs_times: list[float] = []
    try:
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
            if len(data) > _MAX_WS_MESSAGE_SIZE:
                st.log.warning("WS oversized message (%d bytes, %s)", len(data), _client_host)
                await ws.close(code=1009, reason="message too big")
                break
            if data.startswith("{"):
                try:
                    parsed = orjson.loads(data)
                    if not isinstance(parsed, dict):
                        continue
                    if parsed.get("type") == "sync_prefs":
                        now = time.monotonic()
                        _sync_prefs_times[:] = [t for t in _sync_prefs_times if now - t < _SYNC_PREFS_WINDOW]
                        if len(_sync_prefs_times) >= _SYNC_PREFS_MAX:
                            st.log.warning("WS sync_prefs rate limited (%s)", _client_host)
                            continue
                        _sync_prefs_times.append(now)
                        raw_prefs = parsed.get("prefs", {})
                        if not isinstance(raw_prefs, dict):
                            continue
                        prefs = sanitize_prefs(raw_prefs)
                        ws_mgr.set_prefs(ws, prefs)
                except (orjson.JSONDecodeError, ValueError):
                    st.log.warning("Invalid WS message from %s (len=%d)", _client_host, len(data))
    except WebSocketDisconnect as e:
        if e.code in (1000, 1001):
            st.log.info("WS client disconnected (%s, code %d)", _client_host, e.code)
        elif e.code == 1012:
            st.log.info("WS client disconnected - server shutting down (%s)", _client_host)
        else:
            st.log.warning("WS client disconnected (%s, code %d)", _client_host, e.code)
    except Exception as e:
        st.log_error("WS handler error", e)
        try:
            await ws.close(code=1011, reason="internal error")
        except Exception as e2:
            st.log_error("WS close after handler error failed", e2)
    finally:
        ws_mgr.disconnect(ws)
