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

from plotroom.adapters.store_sqlite import github_sql
from plotroom.adapters.store_sqlite.actions_sql import pending_action
from plotroom.adapters.store_sqlite.items_sql import first_change, open_item, open_items
from plotroom.adapters.store_sqlite.lanes_sql import board_lanes, lane_counts
from plotroom.adapters.store_sqlite.ledger_sql import (
    EVENTS_AFTER,
    HEAD_SEQ,
    SqliteTransaction,
    event_from_row,
)
from plotroom.adapters.store_sqlite.migrate import upgrade_to_head
from plotroom.adapters.store_sqlite.work_items_sql import ready_work_items
from plotroom.core.actions import Action
from plotroom.core.events import Event
from plotroom.core.github import FetchCursor, FetchRetry, GitHubSummary, PullRequest
from plotroom.core.github_projection import GitHubResource
from plotroom.core.next_items import Item
from plotroom.ports.store import Board
from plotroom.ports.work_items import WorkItem

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
            handle = SqliteTransaction(connection)
            try:
                try:
                    yield handle
                finally:
                    handle.expire()  # a retained handle must not reach the next input's transaction
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

    async def _board(self, connection: AsyncConnection, role: str) -> Board:
        cursor = int((await connection.execute(HEAD_SEQ)).scalar_one())
        return (
            cursor,
            await open_items(connection, role),
            await lane_counts(connection, role),
            await board_lanes(connection, role),
        )

    async def read_board(self, role: str) -> Board:
        async with self._engine.connect() as connection:
            return await self._board(connection, role)

    async def read_github_board(self, role: str, repository: str) -> tuple[Board, GitHubSummary]:
        async with self._engine.connect() as connection:
            cursor, items, counts, lanes = await self._board(connection, role)
            projected = await github_sql.project_lane_heads(connection, repository, lanes)
            return (cursor, items, counts, projected), await github_sql.summary(
                connection, repository
            )

    async def pull_request(self, repository: str, number: int) -> PullRequest | None:
        async with self._engine.connect() as connection:
            return await github_sql.pull_request(connection, repository, number)

    async def github_resource(
        self, repository: str, resource: str, scope: str = ""
    ) -> GitHubResource | None:
        async with self._engine.connect() as connection:
            return await github_sql.github_resource(connection, repository, resource, scope)

    async def github_pull_targets(self, repository: str) -> tuple[PullRequest, ...]:
        async with self._engine.connect() as connection:
            return await github_sql.github_pull_targets(connection, repository)

    async def fetch_cursor(self, repository: str, resource: str) -> FetchCursor | None:
        async with self._engine.connect() as connection:
            return await github_sql.fetch_cursor(connection, repository, resource)

    async def fetch_retry(self, repository: str, resource: str) -> FetchRetry | None:
        async with self._engine.connect() as connection:
            return await github_sql.fetch_retry(connection, repository, resource)

    async def read_next(self, role: str, offset: int, limit: int) -> tuple[int, list[Item]]:
        async with self._engine.connect() as connection:
            cursor = int((await connection.execute(HEAD_SEQ)).scalar_one())
            return cursor, await open_items(connection, role, offset=offset, limit=limit)

    async def read_open_item(self, role: str, item_id: str) -> Item | None:
        async with self._engine.connect() as connection:
            return await open_item(connection, role, item_id)

    async def first_item_change(self, role: str, cursor: int) -> tuple[int, dict[str, Any] | None]:
        async with self._engine.connect() as connection:
            head = int((await connection.execute(HEAD_SEQ)).scalar_one())
            return head, await first_change(connection, role, cursor)

    async def ready_work_items(self) -> tuple[WorkItem, ...]:
        async with self._engine.connect() as connection:
            return await ready_work_items(connection)

    async def pending_action(self) -> Action | None:
        async with self._engine.connect() as connection:
            return await pending_action(connection)

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        try:
            if writer is not None:
                await writer.close()
        finally:
            await self._engine.dispose()
