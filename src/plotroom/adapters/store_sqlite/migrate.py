"""Alembic wiring: one linear history, run on the store's own connection."""

from __future__ import annotations

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
