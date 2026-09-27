"""Helpers for code that runs the real app in a child process (conftest's run_python).

The child imports them as `scripts.tests.app_child` and reports through emit(); the test
reads that report with result(). backend.main loads its config at import, which is why
these tests run it in a child instead of in the pytest process.
"""
import json
import threading

import anyio
from starlette.websockets import WebSocketDisconnect

RESULT_MARK = "RESULT "


def emit(out) -> None:
    """Report a JSON result to the parent; everything else the child prints is logging."""
    print(RESULT_MARK + json.dumps(out), flush=True)


def result(proc):
    """The result a child emitted."""
    return json.loads(proc.stdout.split(RESULT_MARK, 1)[1])


def receive_until(ws, done, seconds: float) -> list:
    """JSON frames a TestClient websocket receives until done(frames), a close, or `seconds` pass.

    Starlette's receive has no timeout: a server that went quiet (no heartbeat) blocked the
    child until the subprocess timeout and failed with a timeout instead of an assertion (F42).
    The caller asserts on what arrived in time.
    """
    frames = []

    def read():
        try:
            while not done(frames):
                frames.append(ws.receive_json())
        except (WebSocketDisconnect, anyio.EndOfStream):
            # Closed by the server, or by the caller leaving the socket's context after the deadline
            return

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(seconds)
    return list(frames)
