"""The SQLite ``Store``: one WAL-mode file per project, opened for exactly that project (AD-4)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alembic.runtime.migration import MigrationContext
from sqlalchemy import URL, Connection, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry

from plotroom.adapters.store_sqlite.ledger_sql import (
    EVENTS_AFTER,
    HEAD_SEQ,
    SqliteTransaction,
    event_from_row,
)
from plotroom.adapters.store_sqlite.migrate import upgrade_to_head
from plotroom.core.events import Event

BEGIN_OPTION = "plotroom_begin"


def _on_connect(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
    # The driver must not open transactions on its own (it skips BEGIN before DDL); the begin
    # hook below emits BEGIN, so a migration's schema change and its revision commit together.
    dbapi_connection.isolation_level = None


def _on_begin(connection: Connection) -> None:
    # The writer's transactions take the write lock up front (BEGIN IMMEDIATE) through an
    # execution option; every other connection, migrations included, begins deferred.
    mode = connection.get_execution_options().get(BEGIN_OPTION)
    connection.exec_driver_sql("BEGIN IMMEDIATE" if mode == "IMMEDIATE" else "BEGIN")


async def _uninterrupted[T](awaitable: Awaitable[T]) -> T:
    """Await ``awaitable`` to completion however often this coroutine is canceled meanwhile.

    A commit or rollback must not be abandoned halfway; the cancellation is raised afterwards.
    """
    task = asyncio.ensure_future(awaitable)
    canceled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            canceled = True
    result = task.result()
    if canceled:
        raise asyncio.CancelledError
    return result


class SqliteStore:
    """Implements ``plotroom.ports.Store`` over ``<data_dir>/<project_id>.db``."""

    def __init__(self, project_id: str, data_dir: Path) -> None:
        self._project_id = project_id
        self.path = data_dir / f"{project_id}.db"
        self._engine: AsyncEngine = create_async_engine(
            URL.create("sqlite+aiosqlite", database=str(self.path))
        )
        event.listen(self._engine.sync_engine, "connect", _on_connect)
        event.listen(self._engine.sync_engine, "begin", _on_begin)
        self._writer: AsyncConnection | None = None
        self._writing = False

    @property
    def project_id(self) -> str:
        return self._project_id

    async def migrate(self) -> None:
        await upgrade_to_head(self._engine)

    async def revision(self) -> str | None:
        def read(connection: Any) -> str | None:
            return MigrationContext.configure(connection).get_current_revision()

        async with self._engine.connect() as connection:
            return await connection.run_sync(read)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[SqliteTransaction]:
        """The project's one writer transaction, on the one writer connection (AD-3).

        ``BEGIN IMMEDIATE``; commits when the block exits normally and rolls back on any
        exception. Commit and rollback finish even when the caller is canceled.
        """
        if self._writing:
            raise RuntimeError("a writer transaction is already open on this store")
        self._writing = True
        try:
            connection = await self._writer_connection()
            await connection.execution_options(**{BEGIN_OPTION: "IMMEDIATE"})
            try:
                transaction = await connection.begin()
            except BaseException:
                await self._discard_writer()
                raise
            try:
                yield SqliteTransaction(connection)
            except BaseException:
                try:
                    await _uninterrupted(transaction.rollback())
                except Exception:
                    await self._discard_writer()
                raise
            try:
                await _uninterrupted(transaction.commit())
            except BaseException:
                try:
                    await _uninterrupted(transaction.rollback())
                except Exception:
                    await self._discard_writer()
                raise
        finally:
            self._writing = False

    async def _writer_connection(self) -> AsyncConnection:
        if self._writer is None:
            self._writer = await self._engine.connect()
        return self._writer

    async def _discard_writer(self) -> None:
        """Drop a writer connection whose state is unknown; the next transaction opens a new one."""
        connection, self._writer = self._writer, None
        if connection is not None:
            with contextlib.suppress(Exception):
                await _uninterrupted(connection.invalidate())
            with contextlib.suppress(Exception):
                await _uninterrupted(connection.close())

    async def head_seq(self) -> int:
        async with self._engine.connect() as connection:
            return int((await connection.execute(HEAD_SEQ)).scalar_one())

    async def events_after(self, seq: int, limit: int = 100) -> list[Event]:
        async with self._engine.connect() as connection:
            result = await connection.execute(EVENTS_AFTER, {"seq": seq, "limit": limit})
            return [event_from_row(row) for row in result]

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        try:
            if writer is not None:
                await writer.close()
        finally:
            await self._engine.dispose()
