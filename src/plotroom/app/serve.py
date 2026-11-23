"""``plotroom serve``: take the lock, check the floor, migrate, serve (AD-3, AD-4, AD-5, AD-7)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sqlite3
import threading
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

import uvicorn

from plotroom.adapters.mcp import build_app
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.clock import SystemClock
from plotroom.app.config import Config
from plotroom.app.lock import DataDirLock
from plotroom.app.portfile import remove_port, write_port
from plotroom.app.sqlite_floor import floor_problem
from plotroom.app.tokens import TokenRegistry
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger
from plotroom.ports import Clock

log = logging.getLogger("plotroom.serve")


class _Server(uvicorn.Server):
    """uvicorn's server that publishes the address it actually bound."""

    def __init__(
        self, config: uvicorn.Config, started: asyncio.Future[int], data_dir: Path
    ) -> None:
        super().__init__(config)
        self._started_future = started
        self._data_dir = data_dir

    _shutdown_task: asyncio.Task[None] | None = None

    @contextlib.contextmanager
    def defer_sigterm(self) -> Generator[None]:
        """Hold SIGTERM until the daemon is done, including when uvicorn replays it."""
        if threading.current_thread() is not threading.main_thread():
            yield
            return

        received = False

        def defer(_signal: int, _frame: FrameType | None) -> None:
            nonlocal received
            received = True
            self.should_exit = True

        previous = signal.signal(signal.SIGTERM, defer)
        try:
            yield
        finally:
            signal.signal(signal.SIGTERM, previous)
            if received:
                signal.raise_signal(signal.SIGTERM)

    def shutdown_task(self, sockets: Any = None) -> asyncio.Task[None]:
        """The one task that shuts uvicorn down, started on first use by whoever needs it."""
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.ensure_future(self._shut_down(sockets))
        return self._shutdown_task

    async def _shut_down(self, sockets: Any) -> None:
        # Stop publishing the listener as soon as it starts shutting down.
        with contextlib.suppress(OSError):
            remove_port(self._data_dir)
        await uvicorn.Server.shutdown(self, sockets)

    async def shutdown(self, sockets: Any = None) -> None:
        # Shielded: a cancel must not abort a shutdown that is draining requests and lifespan
        await asyncio.shield(self.shutdown_task(sockets))

    async def startup(self, sockets: Any = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and not self._started_future.done():
            bound: Any = self.servers[0].sockets[0].getsockname()
            host = str(bound[0])
            port = int(bound[1])
            # Published before anyone is told the server is up: a client that sees "serving" in
            # the log, or reads the file, finds the same port.
            try:
                write_port(self._data_dir, port, host=host)
            except BaseException:
                # uvicorn skips its own shutdown when startup raises, which would leave the
                # listener open after the lock is released.
                await self.shutdown_task()
                raise
            self._started_future.set_result(port)


async def _finish(task: asyncio.Task[None]) -> None:
    """Wait for ``task`` to complete, however many times this coroutine is cancelled meanwhile."""
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)
    task.result()


@dataclass
class _Project:
    """A project the server has opened: its store and, once migrated, its running ledger."""

    store: SqliteStore
    ledger: Ledger | None = None


class Daemon:
    """One plotroom server for one data directory."""

    def __init__(
        self,
        config: Config,
        *,
        sqlite_version: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._projects: dict[str, _Project] = {}
        self._sqlite_version = (
            sqlite_version if sqlite_version is not None else sqlite3.sqlite_version
        )
        self._started: asyncio.Future[int] | None = None
        self._server: _Server | None = None

    async def wait_started(self) -> int:
        """The port the server is listening on, once it is."""
        if self._started is None:
            self._started = asyncio.get_running_loop().create_future()
        return await self._started

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

    @property
    def ledgers(self) -> dict[str, Ledger]:
        """The running ledger of each opened project (none below the SQLite floor)."""
        return {
            project_id: project.ledger
            for project_id, project in self._projects.items()
            if project.ledger is not None
        }

    async def run(self) -> None:
        """Serve until stopped. Raises ``LockError`` when another server owns the directory."""
        config = self._config
        if self._started is None:
            self._started = asyncio.get_running_loop().create_future()
        with contextlib.ExitStack() as signals, DataDirLock(config.data_dir):
            # Whatever an earlier daemon left behind is not this one's: gone before it binds.
            remove_port(config.data_dir)
            try:
                problem = floor_problem(self._sqlite_version)
                if problem is not None:
                    log.error("not ready", extra={"reason": problem})
                else:
                    await self._open_projects()
                app = build_app(
                    config.projects,
                    config.allowed_hosts,
                    ledgers=self.ledgers,
                    authenticate=TokenRegistry(config.data_dir).authenticate,
                    unavailable=problem,
                )
                uvicorn_config = uvicorn.Config(
                    app,
                    host=config.host,
                    port=config.port,
                    log_config=None,
                    access_log=False,
                )
                self._server = _Server(uvicorn_config, self._started, config.data_dir)
                # Defer the SIGTERM uvicorn replays until the projects have closed and the lock
                # is released. Uvicorn still restores and replays SIGINT on its usual schedule.
                signals.enter_context(self._server.defer_sigterm())
                serving = asyncio.ensure_future(self._announce())
                try:
                    await self._server.serve()
                except asyncio.CancelledError:
                    # uvicorn skips its own shutdown when its main loop is cancelled. Finish the
                    # one shutdown (begun by uvicorn or by us) before the lock is released.
                    if self._server.started:
                        await _finish(self._server.shutdown_task())
                    raise
                finally:
                    serving.cancel()
            finally:
                # Whatever ended the server (stop, cancel, a failed start), the port file goes, then
                # every writer stops and every store closes, all before the lock is released.
                with contextlib.suppress(
                    OSError
                ):  # a leftover file is never trusted without the lock
                    remove_port(config.data_dir)
                await _finish(asyncio.ensure_future(self._close_projects()))
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise asyncio.CancelledError  # a cancel that arrived mid-teardown still counts

    async def _announce(self) -> None:
        port = await self.wait_started()
        log.info(
            "serving",
            extra={
                "host": self._config.host,
                "port": port,
                "projects": list(self._config.projects),
                "data_dir": str(self._config.data_dir),
            },
        )

    async def _open_projects(self) -> None:
        """Migrate each project file and start its ledger (never reached below the SQLite floor)."""
        for project_id in self._config.projects:
            project = _Project(SqliteStore(project_id, self._config.data_dir))
            self._projects[project_id] = project  # registered first so a failure closes it
            await project.store.migrate()
            log.info(
                "migrated",
                extra={"project": project_id, "revision": await project.store.revision()},
            )
            project.ledger = Ledger(project.store, build_catalog(), self._clock)
            project.ledger.start()

    async def _close_projects(self) -> None:
        """Stop every writer (draining queued inputs), then close every store. Never raises."""
        projects = list(self._projects.values())
        for project in projects:
            if project.ledger is not None:
                try:
                    await project.ledger.stop()
                except Exception:
                    log.exception("ledger stop failed", extra={"project": project.store.project_id})
        for project in projects:
            try:
                await project.store.close()
            except Exception:
                log.exception("store close failed", extra={"project": project.store.project_id})
        self._projects.clear()
