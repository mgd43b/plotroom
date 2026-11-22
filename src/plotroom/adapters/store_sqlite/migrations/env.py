"""Alembic environment. Migrations run on a connection the store hands in (AD-5)."""

from __future__ import annotations

from alembic import context

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "migrations run only through plotroom.adapters.store_sqlite.upgrade_to_head(); "
        "use `alembic revision` to add one"
    )

context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
