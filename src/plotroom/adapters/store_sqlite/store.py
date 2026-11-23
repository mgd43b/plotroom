"""The SQLite ``Store``: one WAL-mode file per project, opened for exactly that project (AD-4)."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
import sqlite_vec  # pyright: ignore[reportMissingTypeStubs]
from alembic.runtime.migration import MigrationContext
from sqlalchemy import URL, Connection, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry

from plotroom.adapters.store_sqlite import (
    budgets_sql,
    checklists_sql,
    embedding_sql,
    github_sql,
    outbox_sql,
    triggers_sql,
)
from plotroom.adapters.store_sqlite.actions_sql import pending_actions
from plotroom.adapters.store_sqlite.claims_sql import count_claims, read_claims
from plotroom.adapters.store_sqlite.items_sql import (
    first_change,
    open_item,
    open_items,
    open_overlaps,
)
from plotroom.adapters.store_sqlite.lanes_sql import board_lanes, lane_counts, worker_lane
from plotroom.adapters.store_sqlite.ledger_sql import (
    EVENTS_AFTER,
    HEAD_SEQ,
    SqliteTransaction,
    event_from_row,
)
from plotroom.adapters.store_sqlite.migrate import upgrade_to_head
from plotroom.adapters.store_sqlite.search_sql import search_messages
from plotroom.adapters.store_sqlite.work_items_sql import ready_work_items
from plotroom.core.actions import Action, QueuedAction
from plotroom.core.claims import Claim
from plotroom.core.events import Event, LedgerError
from plotroom.core.github import FetchCursor, FetchRetry, GitHubSummary, PullRequest
from plotroom.core.github_projection import GitHubResource
from plotroom.core.merge_gate import GateSnapshot, load_gate_snapshot
from plotroom.core.next_items import Item
from plotroom.core.search import pack_vector
from plotroom.ports.search import (
    MAX_SEARCH_LIMIT,
    MESSAGE_KIND,
    EmbeddedMessage,
    EmbeddingError,
    EmbeddingModel,
    SearchPage,
)
from plotroom.ports.store import Board, Transaction
from plotroom.ports.work_items import WorkItem

if TYPE_CHECKING:
    from plotroom.core.budgets import Budget
    from plotroom.core.outbox import OutboxReadiness
    from plotroom.core.triggers import Trigger

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


async def _load_vector_extension(connection: AsyncConnection) -> None:
    if connection.info.get("sqlite_vec_loaded"):
        return
    raw = await connection.get_raw_connection()
    driver = raw.driver_connection
    if not isinstance(driver, aiosqlite.Connection):
        raise TypeError("vector search requires an aiosqlite connection")
    await driver.enable_load_extension(True)
    try:
        await driver.load_extension(sqlite_vec.loadable_path())
    finally:
        await driver.enable_load_extension(False)
    connection.info["sqlite_vec_loaded"] = True


@dataclass(frozen=True)
class SqliteEmbeddingIndex:
    """The writer and reader for one model in one project's database."""

    _engine: AsyncEngine
    project_id: str
    model: EmbeddingModel

    async def cursor(self) -> int:
        async with self._engine.connect() as connection:
            return await embedding_sql.read_cursor(connection, self.model)

    async def commit(
        self, tx: Transaction, expected: int, through: int, records: tuple[EmbeddedMessage, ...]
    ) -> bool:
        if not isinstance(tx, SqliteTransaction):
            raise TypeError("the SQLite embedding index requires a SQLite writer transaction")
        return await tx.commit_embedding_batch(self._engine, self.model, expected, through, records)

    async def query_vector(self, project: str, vector: tuple[float, ...], limit: int) -> SearchPage:
        if project != self.project_id:
            raise LedgerError("forbidden", "search is restricted to the current project")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise LedgerError("invalid", f"search limit must be between 1 and {MAX_SEARCH_LIMIT}")
        try:
            query = pack_vector(vector, self.model.dimension)
        except EmbeddingError as exc:
            raise LedgerError("invalid", str(exc)) from exc
        async with self._engine.connect() as connection:
            # Loading is only needed for queries and leaves extension loading disabled again.
            try:
                await _uninterrupted(_load_vector_extension(connection))
            except (sqlite3.Error, OSError, AttributeError) as exc:
                raise LedgerError(
                    "unavailable", "SQLite vector search requires sqlite-vec"
                ) from exc
            return await embedding_sql.query_vectors(connection, self.model, query, limit)


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

    async def index(self, tx: Transaction, event: Event) -> None:
        if event.kind == MESSAGE_KIND:
            if not isinstance(tx, SqliteTransaction):
                raise TypeError("the SQLite index requires a SQLite writer transaction")
            await tx.index_message(self._engine, event)

    async def query(
        self, project: str, text: str, kinds: tuple[str, ...] | None, limit: int
    ) -> SearchPage:
        if project != self._project_id:
            raise LedgerError("forbidden", "search is restricted to the current project")
        if kinds is not None and MESSAGE_KIND not in kinds:
            return SearchPage((), 0)
        async with self._engine.connect() as connection:
            return await search_messages(connection, text, limit)

    def embedding_index(self, model: EmbeddingModel) -> SqliteEmbeddingIndex:
        return SqliteEmbeddingIndex(self._engine, self._project_id, model)

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

    async def read_snapshot(
        self, role: str, repository: str | None, *, checklist_lane: str | None = None
    ) -> tuple[Board, GitHubSummary | None, dict[str, int], int, str | None]:
        async with self._engine.connect() as connection:
            board, github = (
                (await self._board(connection, role), None)
                if repository is None
                else await self._github_board(connection, role, repository)
            )
            if checklist_lane is not None:
                _, _, _, lanes = board
                for index, lane in enumerate(lanes):
                    if lane.id == checklist_lane:
                        checklist = await checklists_sql.find_checklist(connection, lane.id)
                        lanes[index] = lane.with_checklist(checklist)
                        break
            trigger_counts = await triggers_sql.trigger_counts(connection)
            claim_count = await count_claims(connection, role)
            freeze_reason = await SqliteTransaction(connection).deploy_freeze()
            return board, github, trigger_counts, claim_count, freeze_reason

    async def read_github_board(self, role: str, repository: str) -> tuple[Board, GitHubSummary]:
        async with self._engine.connect() as connection:
            return await self._github_board(connection, role, repository)

    async def _github_board(
        self, connection: AsyncConnection, role: str, repository: str
    ) -> tuple[Board, GitHubSummary]:
        cursor, items, counts, lanes = await self._board(connection, role)
        projected = await github_sql.project_lane_heads(connection, repository, lanes)
        return (cursor, items, counts, projected), await github_sql.summary(connection, repository)

    async def read_claims(
        self, role: str, lane: str | None, offset: int, limit: int
    ) -> tuple[int, list[Claim]]:
        async with self._engine.connect() as connection:
            cursor = int((await connection.execute(HEAD_SEQ)).scalar_one())
            return cursor, await read_claims(connection, role, lane, offset, limit)

    async def read_brief_claims(self, worker: str) -> tuple[str | None, list[Claim]]:
        async with self._engine.connect() as connection:
            lane = await worker_lane(connection, worker)
            return lane.id if lane else None, await read_claims(connection, "orchestrator")

    async def read_overlaps(
        self, role: str, lane: str | None, pr: int | None, offset: int, limit: int
    ) -> tuple[int, list[Item]]:
        async with self._engine.connect() as connection:
            cursor = int((await connection.execute(HEAD_SEQ)).scalar_one())
            return cursor, await open_overlaps(connection, role, lane, pr, offset, limit)

    async def list_triggers(self, lane: str | None) -> list[Trigger]:
        async with self._engine.connect() as connection:
            return await triggers_sql.list_triggers(connection, lane)

    async def read_budgets(self, run: str | None) -> list[Budget]:
        async with self._engine.connect() as connection:
            return await budgets_sql.read_budgets(connection, run)

    async def read_merge_gate(self, repository: str | None, number: int) -> GateSnapshot:
        async with self._engine.connect() as connection, connection.begin():
            return await load_gate_snapshot(SqliteTransaction(connection), repository, number)

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
        page = await self.pending_actions(after=0, limit=1)
        return page[0].action if page else None

    async def pending_actions(
        self,
        *,
        after: int,
        limit: int,
        skip_branch_updates: bool = False,
        skip_controller_merges: bool = False,
    ) -> tuple[QueuedAction, ...]:
        async with self._engine.connect() as connection:
            return await pending_actions(
                connection,
                after=after,
                limit=limit,
                skip_branch_updates=skip_branch_updates,
                skip_controller_merges=skip_controller_merges,
            )

    async def outbox_readiness(self, now: str) -> OutboxReadiness:
        async with self._engine.connect() as connection:
            head = int((await connection.execute(HEAD_SEQ)).scalar_one())
            return await outbox_sql.readiness(connection, head, now)

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        try:
            if writer is not None:
                await writer.close()
        finally:
            await self._engine.dispose()
