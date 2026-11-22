"""``plotroom serve``: take the lock, check the floor, migrate, serve (AD-3, AD-4, AD-5, AD-7)."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

import uvicorn

from plotroom.adapters.mcp import build_app
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.config import Config
from plotroom.app.lock import DataDirLock
from plotroom.app.sqlite_floor import floor_problem

log = logging.getLogger("plotroom.serve")


class _Server(uvicorn.Server):
    """uvicorn's server that reports the port it actually bound."""

    def __init__(self, config: uvicorn.Config, started: asyncio.Future[int]) -> None:
        super().__init__(config)
        self._started_future = started

    async def startup(self, sockets: Any = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and not self._started_future.done():
            bound: Any = self.servers[0].sockets[0].getsockname()
            self._started_future.set_result(int(bound[1]))


class Daemon:
    """One plotroom server for one data directory."""

    def __init__(self, config: Config, *, sqlite_version: str | None = None) -> None:
        self._config = config
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

    async def run(self) -> None:
        """Serve until stopped. Raises ``LockError`` when another server owns the directory."""
        config = self._config
        if self._started is None:
            self._started = asyncio.get_running_loop().create_future()
        with DataDirLock(config.data_dir):
            problem = floor_problem(self._sqlite_version)
            if problem is not None:
                log.error("not ready", extra={"reason": problem})
            else:
                await self._migrate_projects()
            app = build_app(config.projects, config.allowed_hosts, unavailable=problem)
            uvicorn_config = uvicorn.Config(
                app,
                host=config.host,
                port=config.port,
                log_config=None,
                access_log=False,
            )
            self._server = _Server(uvicorn_config, self._started)
            serving = asyncio.ensure_future(self._announce())
            try:
                await self._server.serve()
            finally:
                serving.cancel()

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

    async def _migrate_projects(self) -> None:
        for project_id in self._config.projects:
            store = SqliteStore(project_id, self._config.data_dir)
            try:
                await store.migrate()
                log.info(
                    "migrated", extra={"project": project_id, "revision": await store.revision()}
                )
            finally:
                await store.close()
