import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore, head_revision, script_heads
from plotroom.adapters.store_sqlite.migrate import MIGRATIONS_DIR, alembic_config, upgrade_to_head
from plotroom.core.catalog import build_catalog
from plotroom.core.events import NewEvent
from plotroom.core.github import ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork


def test_history_has_exactly_one_head() -> None:
    """CI fails on more than one head: parallel entries must re-parent onto main's head."""
    assert len(script_heads()) == 1


def test_history_is_linear() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    for revision in script.walk_revisions():
        parents = revision.down_revision
        assert parents is None or isinstance(parents, str), f"{revision.revision} is a merge"


async def test_a_new_file_is_migrated_to_head(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    try:
        assert await store.revision() is None
        await store.migrate()
        assert await store.revision() == head_revision()
    finally:
        await store.close()
    assert store.path == tmp_path / "demo.db"
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"project_meta", "alembic_version", "events", "project_seq"} <= tables


async def test_migrating_twice_changes_nothing(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    try:
        await store.migrate()
        await store.migrate()
        assert await store.revision() == head_revision()
    finally:
        await store.close()


async def test_a_file_with_an_unknown_revision_is_not_migrated(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        db.execute("INSERT INTO alembic_version VALUES ('from-the-future')")
    store = SqliteStore("demo", tmp_path)
    try:
        with pytest.raises(Exception, match="from-the-future"):
            await store.migrate()
    finally:
        await store.close()


def test_two_revisions_with_the_same_parent_are_detected(tmp_path: Path) -> None:
    """The check CI relies on: a parallel entry that forgot to re-parent makes a second head."""
    copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    head = head_revision()
    (copy / "versions" / "9998_parallel.py").write_text(
        'revision = "parallel"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n\n'
        "def upgrade() -> None:\n    pass\n",
        encoding="utf-8",
    )
    cfg = alembic_config()
    cfg.set_main_option("script_location", str(copy))
    assert sorted(script_heads(cfg)) == sorted([head, "parallel"])
    with pytest.raises(RuntimeError, match="exactly one head"):
        head_revision(cfg)


async def test_a_data_directory_with_url_special_characters_works(tmp_path: Path) -> None:
    odd = tmp_path / "a?b#c%d"
    odd.mkdir()
    store = SqliteStore("demo", odd)
    try:
        await store.migrate()
        assert await store.revision() == head_revision()
    finally:
        await store.close()
    assert (odd / "demo.db").exists()


def test_an_installation_path_with_a_percent_sign_still_loads_the_history(tmp_path: Path) -> None:
    copy = tmp_path / "100%done" / "migrations"
    shutil.copytree(MIGRATIONS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert script_heads(alembic_config(copy)) == script_heads()


async def test_a_migration_that_fails_after_its_first_ddl_leaves_nothing_behind(
    tmp_path: Path,
) -> None:
    """DDL and the revision commit together, so the next startup does not trip on a half migration."""
    copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    head = head_revision()
    broken = copy / "versions" / "9999_half.py"
    broken.write_text(
        'import sqlalchemy as sa\nfrom alembic import op\n\nrevision = "half"\n'
        f'down_revision = "{head}"\nbranch_labels = None\ndepends_on = None\n\n\n'
        "def upgrade() -> None:\n"
        '    op.create_table("half", sa.Column("id", sa.Integer(), primary_key=True))\n'
        '    raise RuntimeError("stopped after the first DDL statement")\n',
        encoding="utf-8",
    )
    cfg = alembic_config()
    cfg.set_main_option("script_location", str(copy))
    store = SqliteStore("demo", tmp_path)
    try:
        await store.migrate()  # real history: at its head
        with pytest.raises(RuntimeError, match="first DDL"):
            await upgrade_to_head(store._engine, cfg)  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == head
    finally:
        await store.close()
    with sqlite3.connect(tmp_path / "demo.db") as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half" not in tables
    # the same revision, repaired, runs from the state the failure left
    broken.write_text(
        'import sqlalchemy as sa\nfrom alembic import op\n\nrevision = "half"\n'
        f'down_revision = "{head}"\nbranch_labels = None\ndepends_on = None\n\n\n'
        "def upgrade() -> None:\n"
        '    op.create_table("half", sa.Column("id", sa.Integer(), primary_key=True))\n',
        encoding="utf-8",
    )
    again = SqliteStore("demo", tmp_path)
    try:
        await upgrade_to_head(again._engine, cfg)  # pyright: ignore[reportPrivateUsage]
        assert await again.revision() == "half"
    finally:
        await again.close()
    with sqlite3.connect(tmp_path / "demo.db") as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half" in tables


async def test_a_file_at_the_first_revision_is_upgraded_in_place(tmp_path: Path) -> None:
    """The event log arrives by migration: an existing 0001 file gains its tables and counter."""
    first = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, first, ignore=shutil.ignore_patterns("__pycache__"))
    for later in (first / "versions").glob("*.py"):
        if not later.name.startswith("0001_"):
            later.unlink()
    cfg = alembic_config()
    cfg.set_main_option("script_location", str(first))
    store = SqliteStore("demo", tmp_path)
    try:
        await upgrade_to_head(store._engine, cfg)  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == "0001"
        await store.migrate()
        assert await store.revision() == head_revision()
        assert await store.head_seq() == 0
        assert await store.events_after(0) == []
    finally:
        await store.close()
    with sqlite3.connect(tmp_path / "demo.db") as db:
        assert db.execute("SELECT id, value FROM project_seq").fetchall() == [(1, 0)]


async def test_trigger_migration_preserves_projected_prs_and_pending_actions(
    tmp_path: Path,
) -> None:
    original = tmp_path / "before-triggers"
    shutil.copytree(MIGRATIONS_DIR, original, ignore=shutil.ignore_patterns("__pycache__"))
    for path in (original / "versions").glob("[0-9]*_*.py"):
        if path.name.split("_", 1)[0] > "0010":
            path.unlink()
    config = alembic_config(original)
    repo = "fixture/project"
    store = SqliteStore("demo", tmp_path)
    catalog = replace(build_catalog(github_repository=repo), trigger_leaves=None)
    ledger = Ledger(store, catalog, FixedClock(), github_repository=repo)
    timestamp = "2026-11-23T12:00:00.000000Z"

    async def seed(uow: UnitOfWork) -> str:
        pull = ObservedPull(7, "fixture", "a" * 40, repo, "merged", timestamp, "before upgrade")
        assert await apply_pull_poll(uow, repo, None, (pull,), timestamp)
        cause = (await uow.append(NewEvent("core", "core.tick", "before-upgrade"))).event
        return await uow.enqueue_action(cause, "fixture", {"value": "keep"})

    try:
        await upgrade_to_head(store._engine, config)  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == "0010"
        ledger.start()
        key = await ledger.run("system", seed)
        await ledger.stop()
        await store.migrate()
        assert await store.revision() == head_revision()
        projected = await store.pull_request(repo, 7)
        assert projected is not None
        assert projected.state == "merged"
        action = await store.pending_action()
        assert action is not None
        assert (action.key, action.payload) == (key, {"value": "keep"})
        assert not await store.list_triggers(None)
        async with store.transaction() as tx:
            assert await tx.first_matching_pull(repo, "fixture*") == 7
    finally:
        await ledger.stop()
        await store.close()
