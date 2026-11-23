"""The SQLite ``Store``: one WAL-mode file per project, opened for exactly that project (AD-4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.runtime.migration import MigrationContext
from sqlalchemy import URL, Connection, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry

from plotroom.adapters.store_sqlite.migrate import upgrade_to_head


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
    connection.exec_driver_sql("BEGIN")


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

    async def close(self) -> None:
        await self._engine.dispose()
