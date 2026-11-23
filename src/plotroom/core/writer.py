"""The per-project writer (AD-3, AD-14): one task, one connection, one transaction per input.

Inputs queue in arrival order and each runs in its own transaction on the one task that owns the
project's writer connection. A failed input rolls back and leaves the writer serving the next.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Awaitable, Callable
from typing import Any

from plotroom.core.events import LedgerError
from plotroom.ports.store import Store, Transaction

type _Item = tuple[Callable[[Transaction], Awaitable[Any]], asyncio.Future[Any]]


# The writer whose transaction the current task is running work in, if any
_WORKING_FOR: contextvars.ContextVar[ProjectWriter | None] = contextvars.ContextVar(
    "plotroom_working_for", default=None
)


def _unavailable() -> LedgerError:
    return LedgerError("unavailable", "the project's ledger writer is not running")


class ProjectWriter:
    """Serializes every write to one project's store."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._queue: asyncio.Queue[_Item | None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._accepting = False

    @property
    def running(self) -> bool:
        return self._accepting

    def start(self) -> None:
        """Start the writer task. Needs a running event loop."""
        if self._task is not None and not self._task.done():
            raise RuntimeError("the writer is already running")
        queue: asyncio.Queue[_Item | None] = asyncio.Queue()
        self._queue = queue
        self._accepting = True
        self._task = asyncio.get_running_loop().create_task(self._run(queue))

    async def stop(self) -> None:
        """Stop accepting inputs, finish the queued ones, then end the task.

        Canceling the caller does not cancel the draining: the task is awaited shielded.
        """
        queue, task = self._queue, self._task
        if task is None or queue is None:
            return
        if self._accepting:
            self._accepting = False
            queue.put_nowait(None)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelled():  # the caller was canceled, not the writer task
                raise

    async def submit[T](self, work: Callable[[Transaction], Awaitable[T]]) -> T:
        """Run ``work`` in its own transaction on the writer and return its result.

        ``LedgerError("unavailable")`` when the writer is not running. A caller canceled while
        queued is skipped; one canceled while its transaction runs does not interrupt it.
        ``RuntimeError`` when called from inside work running on this writer: it would wait
        behind itself.
        """
        if _WORKING_FOR.get() is self:
            raise RuntimeError("re-entrant submit: use the UnitOfWork inside work")
        queue = self._queue
        if not self._accepting or queue is None:
            raise _unavailable()
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        queue.put_nowait((work, future))  # no await before this: arrival order is call order
        return await future

    async def _run(self, queue: asyncio.Queue[_Item | None]) -> None:
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                work, future = item
                if future.cancelled():
                    continue
                try:
                    async with self._store.transaction() as tx:
                        token = _WORKING_FOR.set(self)
                        try:
                            result = await work(tx)
                        finally:
                            _WORKING_FOR.reset(token)
                except Exception as exc:
                    if not future.done():
                        future.set_exception(exc)
                except BaseException:
                    if not future.done():
                        future.set_exception(_unavailable())
                    raise
                else:
                    if not future.done():
                        future.set_result(result)
        finally:
            self._accepting = False
            while not queue.empty():
                leftover = queue.get_nowait()
                if leftover is not None and not leftover[1].done():
                    leftover[1].set_exception(_unavailable())
