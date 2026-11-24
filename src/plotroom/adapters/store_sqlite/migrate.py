"""Alembic wiring: one linear history, run on the store's own connection."""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import time
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(script_location: Path = MIGRATIONS_DIR) -> Config:
    cfg = Config()
    # Alembic reads options through ConfigParser, where a literal "%" must be written "%%"
    cfg.set_main_option("script_location", str(script_location).replace("%", "%%"))
    return cfg


def script_heads(cfg: Config | None = None) -> list[str]:
    """Every head in the history; CI fails when there is more than one."""
    return list(ScriptDirectory.from_config(cfg or alembic_config()).get_heads())


def head_revision(cfg: Config | None = None) -> str:
    heads = script_heads(cfg)
    if len(heads) != 1:
        raise RuntimeError(f"migration history must have exactly one head, found {heads}")
    return heads[0]


def _upgrade(connection: Connection, cfg: Config) -> None:
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


async def upgrade_to_head(engine: AsyncEngine, cfg: Config | None = None) -> None:
    """Bring the project file the engine points at to the head revision."""
    cfg = cfg or alembic_config()
    head_revision(cfg)
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade, cfg)


class ProjectMigrationError(RuntimeError):
    """A project file cannot be safely opened or migrated."""


def _current_revision(source: sqlite3.Connection) -> str | None:
    table = source.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'alembic_version'"
    ).fetchone()
    if table is None:
        return None
    rows = source.execute("SELECT version_num FROM alembic_version LIMIT 2").fetchall()
    if not rows:
        return None
    revision = rows[0][0]
    if len(rows) != 1 or not isinstance(revision, str):
        raise ProjectMigrationError("invalid project schema revision record")
    return revision


def _backup(source: sqlite3.Connection, path: Path) -> None:
    directory = path.parent / "migration-backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ProjectMigrationError(
            "migration backup directory must be private and owned by the daemon"
        )

    fd, name = tempfile.mkstemp(prefix=f".{path.name}.pre-migration-", suffix=".tmp", dir=directory)
    staging = Path(name)
    try:
        # Bound a backup blocked by another SQLite connection. Successful page copies can continue.
        busy_until = time.monotonic() + 5

        def progress(status: int, _remaining: int, _total: int) -> None:
            nonlocal busy_until
            if status == sqlite3.SQLITE_OK:
                busy_until = time.monotonic() + 5
            elif (
                status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                and time.monotonic() > busy_until
            ):
                raise ProjectMigrationError("SQLite backup could not obtain a read lock")

        with closing(sqlite3.connect(staging)) as target:
            source.backup(target, pages=256, progress=progress)
            # The published backup must be a single self-contained file, even for a WAL source.
            if target.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise ProjectMigrationError("SQLite backup could not leave WAL mode")
        os.fsync(fd)
        backup = staging.with_name(staging.name.removeprefix(".").removesuffix(".tmp") + ".bak")
        os.link(staging, backup)
        # Sync both the backup entry and the directory entry created on the first backup.
        for parent in (directory, path.parent):
            directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        os.close(fd)
        staging.unlink(missing_ok=True)


def prepare_upgrade(path: Path, cfg: Config) -> None:
    """Inspect an existing file read-only and back it up before allowing schema changes."""
    try:
        path.stat()
    except FileNotFoundError:
        return
    head = head_revision(cfg)
    script = ScriptDirectory.from_config(cfg)
    try:
        with closing(sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)) as source:
            current = _current_revision(source)
            if current is not None and current != head:
                known = {item.revision for item in script.walk_revisions()}
                if current not in known:
                    if (
                        current.isascii()
                        and current.isdecimal()
                        and len(current) <= 32
                        and head.isascii()
                        and head.isdecimal()
                        and int(current) > int(head)
                    ):
                        raise ProjectMigrationError(
                            f"project schema revision {current} is newer "
                            f"than supported revision {head}"
                        )
                    raise ProjectMigrationError("unknown project schema revision")
            if source.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
                raise ProjectMigrationError("SQLite project integrity check failed")
            if current == head:
                return
            try:
                _backup(source, path)
            except (OSError, sqlite3.Error) as exc:
                raise ProjectMigrationError(
                    "could not create the pre-migration SQLite backup"
                ) from exc
    except sqlite3.Error as exc:
        raise ProjectMigrationError("cannot read the SQLite project database") from exc
