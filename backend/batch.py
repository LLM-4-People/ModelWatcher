"""Shared base class for periodic batch flushers.

Provides the start/stop lifecycle and flush loop shared by WriteBatcher
(db.py, batched SQLite writes) and BroadcastBatcher (scheduler.py,
batched WebSocket broadcasts). Subclasses implement flush().
"""

import asyncio
import logging

_log = logging.getLogger("modelwatcher")


class PeriodicBatcher:
    """Base class for async batched processors that flush on interval.

    Subclasses must implement flush(). The start/stop lifecycle and
    _flush_loop are shared across WriteBatcher (db.py) and
    BroadcastBatcher (scheduler.py).
    """

    def __init__(self, flush_interval: float = 2.0):
        self._flush_interval = flush_interval
        self._task: asyncio.Task | None = None

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._flush_loop())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.flush()

    async def _flush_loop(self):
        while True:
            try:
                await asyncio.sleep(self._flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.error("%s loop error: %s", type(self).__name__, e, exc_info=True)
                await asyncio.sleep(self._flush_interval)

    async def flush(self):
        raise NotImplementedError
