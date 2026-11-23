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


def include_object(
    _object: object, name: str | None, type_: str, _reflected: bool, _compare_to: object
) -> bool:
    # The hand-written FTS migration owns the virtual table and its shadow tables.
    return type_ != "table" or name not in {
        "message_fts",
        "message_fts_config",
        "message_fts_content",
        "message_fts_data",
        "message_fts_docsize",
        "message_fts_idx",
    }


context.configure(
    connection=connection,
    target_metadata=config.attributes.get("target_metadata"),
    include_object=include_object,
)
with context.begin_transaction():
    context.run_migrations()
